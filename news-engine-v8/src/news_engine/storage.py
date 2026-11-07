"""Two business tables, compact execution metadata and no migration machinery."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Article, ArticleInput, Error, Event, RunResult
from .id_constraints import IDConstraintError

MARKER = "news-engine-mvp-8"


class BusyError(RuntimeError):
    pass


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or any(char.isspace() or ord(char) < 32 for char in url)):
        raise ValueError("Article URL must be an HTTP(S) URL without credentials")
    port = parts.port
    host = parts.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    if port is not None and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
        host += ":" + str(port)
    query = sorted((key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
                   if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"})
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", urlencode(query), ""))


def content_hash(text: str) -> str:
    return sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path=Path("data/news-mvp-v8.sqlite")):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing_nonempty = self.path.exists() and self.path.stat().st_size > 0
        self.db = sqlite3.connect(self.path, timeout=1)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        try:
            tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError:
            self.db.close()
            raise ValueError("Not a News MVP database; use a fresh database path") from None
        if tables:
            try:
                marker = self.db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
            except sqlite3.DatabaseError:
                marker = None
            if marker is None or marker[0] != MARKER:
                self.db.close()
                raise ValueError("Not a News MVP database; use a fresh database path")
        elif existing_nonempty:
            self.db.close()
            raise ValueError("Not a marked News MVP database; use a fresh database path")
        else:
            with self._file_lock():
                # Another process may have initialized this new file while we waited.
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    self.db.close()
                    raise BusyError("Database was initialized by another writer; retry")
                self.db.executescript("""
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE articles(article_id TEXT PRIMARY KEY, normalized_url TEXT NOT NULL,
                        content_hash TEXT NOT NULL UNIQUE, data TEXT NOT NULL, state TEXT NOT NULL);
                    CREATE TABLE events(event_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                    CREATE TABLE article_event(article_id TEXT PRIMARY KEY REFERENCES articles(article_id),
                        event_id TEXT NOT NULL REFERENCES events(event_id));
                    CREATE TABLE runs(run_id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT);
                    CREATE TABLE search_history(query TEXT PRIMARY KEY, completed_at TEXT NOT NULL);
                """)
                self.db.execute("INSERT INTO metadata VALUES('schema',?)", (MARKER,))
                self.db.commit()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.db.close()

    @contextmanager
    def _file_lock(self):
        lock_path = Path(str(self.path) + ".lock")
        with lock_path.open("a+b") as lock:
            if lock.seek(0, 2) == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise BusyError("News pipeline is busy") from None
            try:
                yield
            finally:
                lock.seek(0)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def writer_lock(self):
        with self._file_lock():
            for row in self.db.execute("SELECT run_id FROM runs WHERE status='running'").fetchall():
                self.save_run(RunResult(run_id=row[0], status="failed", errors=[Error(
                    stage="run", code="interrupted", message="Previous execution was interrupted")]))
            yield

    def progress(self, run_id, stage, completed=0, total=None):
        path = Path(str(self.path) + ".progress.json")
        now = datetime.now(timezone.utc).isoformat()
        previous = {}
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        payload = dict(run_id=run_id, stage=stage, completed=completed, total=total,
                       started_at=previous.get("started_at", now) if previous.get("run_id") == run_id else now,
                       updated_at=now)
        pending = path.with_suffix(".pending")
        pending.write_text(json.dumps(payload), encoding="utf-8")
        pending.replace(path)

    def ensure_policy(self, fingerprint):
        row = self.db.execute("SELECT value FROM metadata WHERE key='policy'").fetchone()
        if row is not None and row[0] != fingerprint:
            raise ValueError("Event classification policy changed; use a fresh MVP database")
        if row is None:
            with self.db:
                self.db.execute("INSERT INTO metadata VALUES('policy',?)", (fingerprint,))

    def add_article(self, value: ArticleInput) -> Article:
        value = ArticleInput.model_validate(value)
        normalized = normalize_url(value.url)
        if not value.title.strip() or not value.text.strip():
            raise ValueError("Article title and text must be nonempty")
        digest = content_hash(value.text)
        row = self.db.execute("SELECT data FROM articles WHERE content_hash=?", (digest,)).fetchone()
        if row:
            return Article.model_validate_json(row[0])
        article = Article(**value.model_dump(), article_id="A" + sha256((normalized + digest).encode()).hexdigest()[:24],
                          discovered_at=datetime.now(timezone.utc), normalized_url=normalized, content_hash=digest)
        with self.db:
            self.db.execute("INSERT INTO articles VALUES(?,?,?,?,?)", (
                article.article_id, normalized, digest, article.model_dump_json(), "pending"))
        return article

    def articles(self):
        return [Article.model_validate_json(row[0]) for row in self.db.execute("SELECT data FROM articles ORDER BY rowid")]

    def classification_status(self, article_id):
        row = self.db.execute("SELECT state FROM articles WHERE article_id=?", (article_id,)).fetchone()
        if row is None:
            raise KeyError(article_id)
        return row[0]

    def save_group_proposals(self, article_ids, proposals, errors=(), failed=(), irrelevant=()):
        supplied=set(article_ids); failed=set(failed); irrelevant=set(irrelevant)
        if not supplied or len(supplied)!=len(article_ids) or not (failed | irrelevant)<=supplied or failed & irrelevant:
            raise ValueError('Invalid group source states')
        if any(self.classification_status(key) in {'accepted','irrelevant'} for key in supplied):
            raise ValueError('Group contains already classified sources')
        assigned=[key for proposal in proposals for key in proposal.event.article_ids]
        if len(assigned)!=len(set(assigned)) or set(assigned)!=supplied-failed-irrelevant:
            raise IDConstraintError('Each relevant group Article requires exactly one proposal',
                missing_ids=sorted((supplied-failed-irrelevant)-set(assigned)),
                foreign_ids=sorted(set(assigned)-(supplied-failed-irrelevant)),
                duplicate_ids=sorted({key for key in assigned if assigned.count(key)>1}))
        pending_ids={key for item in self.pending_proposals() for key in item.event.article_ids}
        if set(assigned) & pending_ids:
            raise IDConstraintError('Article already has a pending proposal', duplicate_ids=sorted(set(assigned)&pending_ids))
        with self.db:
            for proposal in proposals:
                self.db.execute('INSERT INTO metadata VALUES(?,?)',('pending:'+proposal.proposal_key,proposal.model_dump_json()))
            for article_id in article_ids:
                self.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',('classification_errors:'+article_id,json.dumps([e.model_dump(mode='json') for e in errors])))
                state='failed' if article_id in failed else 'irrelevant' if article_id in irrelevant else 'accepted'
                self.db.execute('UPDATE articles SET state=? WHERE article_id=?',(state,article_id))

    def classification_errors(self, article_id):
        row=self.db.execute('SELECT value FROM metadata WHERE key=?',('classification_errors:'+article_id,)).fetchone()
        return [Error.model_validate(value) for value in json.loads(row[0])] if row else []

    def pending_proposals(self):
        from .group_first import StoredProposal
        return [StoredProposal.model_validate_json(row[0]) for row in self.db.execute("SELECT value FROM metadata WHERE key LIKE 'pending:%' ORDER BY rowid")]

    def fail_article(self, article_id):
        with self.db:
            self.db.execute("UPDATE articles SET state='failed' WHERE article_id=? AND state NOT IN ('accepted','irrelevant')",(article_id,))

    def assignments(self):
        return dict(self.db.execute('SELECT article_id,event_id FROM article_event'))

    def _save_event(self,event):
        event=Event.model_validate(event.model_dump())
        previous=self.db.execute('SELECT data FROM events WHERE event_id=?',(event.event_id,)).fetchone()
        if previous:
            old=Event.model_validate_json(previous[0])
            if not set(old.article_ids)<=set(event.article_ids):
                raise ValueError('Existing Event Article associations cannot be removed')
            if event.category != old.category:
                raise ValueError('Established Event category cannot be changed')
        for article_id in event.article_ids:
            if not self.db.execute('SELECT 1 FROM articles WHERE article_id=?',(article_id,)).fetchone():
                raise IDConstraintError('Event references unknown Article', foreign_ids=[article_id], event_id=event.event_id)
            row=self.db.execute('SELECT event_id FROM article_event WHERE article_id=?',(article_id,)).fetchone()
            if row and row[0]!=event.event_id:
                raise IDConstraintError('Article already belongs to another Event', duplicate_ids=[article_id], existing_event_id=row[0], attempted_event_id=event.event_id)
        self.db.execute('INSERT INTO events VALUES(?,?) ON CONFLICT(event_id) DO UPDATE SET data=excluded.data',(event.event_id,event.model_dump_json()))
        for article_id in event.article_ids:
            self.db.execute('INSERT OR IGNORE INTO article_event VALUES(?,?)',(article_id,event.event_id))

    def save_event(self,event):
        with self.db:self._save_event(event)

    def save_association(self,proposal_keys,event):
        from .group_first import StoredProposal
        if not proposal_keys or len(proposal_keys)!=len(set(proposal_keys)):
            raise ValueError('Association must consume unique pending proposals')
        with self.db:
            incoming=[]
            for key in proposal_keys:
                row=self.db.execute('SELECT value FROM metadata WHERE key=?',('pending:'+key,)).fetchone()
                if row is None:raise KeyError(key)
                incoming.extend(StoredProposal.model_validate_json(row[0]).event.article_ids)
            if len(incoming)!=len(set(incoming)):
                raise IDConstraintError('Merged proposals contain duplicate Article assignments',
                    duplicate_ids=sorted({key for key in incoming if incoming.count(key)>1}))
            old=self.db.execute('SELECT data FROM events WHERE event_id=?',(event.event_id,)).fetchone()
            expected=set(incoming) | (set(Event.model_validate_json(old[0]).article_ids) if old else set())
            if set(event.article_ids)!=expected:
                raise IDConstraintError('Association must preserve all and only contributing Articles',
                    missing_ids=sorted(expected-set(event.article_ids)), foreign_ids=sorted(set(event.article_ids)-expected))
            self._save_event(event)
            for key in proposal_keys:self.db.execute('DELETE FROM metadata WHERE key=?',('pending:'+key,))

    def events(self):
        return [Event.model_validate_json(row[0]) for row in self.db.execute('SELECT data FROM events ORDER BY rowid')]

    def history(self):
        result = [dict(row) for row in self.db.execute("SELECT query,completed_at FROM search_history ORDER BY completed_at")]
        for item in result:
            row = self.db.execute("SELECT value FROM metadata WHERE key=?", ('query:'+item['query'],)).fetchone()
            if row:
                item.update(json.loads(row[0]))
        return result

    def search_since(self, query):
        row = self.db.execute("SELECT completed_at FROM search_history WHERE query=?", (query,)).fetchone()
        return row[0] if row else None

    def record_search(self, query, completed_at, attributes=None):
        with self.db:
            self.db.execute("INSERT INTO search_history VALUES(?,?) ON CONFLICT(query) DO UPDATE SET completed_at=excluded.completed_at",
                            (query, completed_at))
            if attributes:
                self.db.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)",
                                ('query:'+query, json.dumps(attributes)))

    def start_run(self, run_id):
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,'running',NULL)", (run_id,))

    def save_run(self, result: RunResult):
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET status=excluded.status,data=excluded.data",
                            (result.run_id, result.status, result.model_dump_json()))

    def get_run(self, run_id):
        row = self.db.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row[0] is None:
            raise BusyError("Run is still executing")
        return RunResult.model_validate_json(row[0])
