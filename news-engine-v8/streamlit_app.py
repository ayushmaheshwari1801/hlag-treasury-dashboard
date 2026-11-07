"""Read-only viewer for saved News Engine results; no provider dependencies."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from textwrap import shorten

import streamlit as st


ROOT = Path(__file__).resolve().parent
CATEGORIES = {"regulatory": "Regulatory", "macroeconomics": "Macroeconomics", "industry": "Industry"}


def display_text(text):
    for encoding in ("latin-1", "cp1252"):
        try:
            return text.encode(encoding).decode("utf-8")
        except UnicodeError:
            pass
    return text


def summary_paragraphs(text):
    paragraphs = [part.strip() for part in re.split(r'\n\s*\n', display_text(text)) if part.strip()]
    if len(paragraphs) != 1 or len(paragraphs[0]) < 500:
        return paragraphs
    sentences = []
    for part in re.split(r'(?<=[.!?])\s+(?=[A-Z])', paragraphs[0]):
        if sentences and re.search(r'(?:\b(?:[A-Za-z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|Prof|Inc|Ltd|No)\.)$', sentences[-1]):
            sentences[-1] += ' ' + part
        else:
            sentences.append(part)
    return [' '.join(sentences[index:index+2]) for index in range(0, len(sentences), 2)]


def run_overview(events):
    if not events:
        return "No saved Events for this run."
    leaders = events[:3]
    titles = []
    for event in leaders:
        title = shorten(" ".join(display_text(event["title"]).split()), width=96, placeholder="…")
        score = event.get("score")
        titles.append(title + (f" (R5 {score['total']})" if score else ""))
    scored = sum(bool(event.get("score")) for event in events)
    overview = f"{len(events)} Events, {scored} scored. Priorities: " + "; ".join(titles) + "."
    for label, field in [("Potential impact", "why_it_matters"), ("Follow-up", "what_to_do")]:
        text = next((text for event in leaders for text in event.get(field, []) if text.strip()), "")
        if text:
            addition = f" {label}: " + " ".join(display_text(text).split())
            if len(overview + addition) <= 700:
                overview += addition
    return overview


def read_result(text):
    result = json.loads(text)
    if not isinstance(result, dict) or not isinstance(result.get("run_id"), str):
        raise ValueError("This file is not a saved RunResult")
    if not isinstance(result.get("events"), list) or not isinstance(result.get("sources"), list):
        raise ValueError("RunResult must contain events and sources lists")
    return result


def result_files(root):
    paths, errors = [], []
    for path in sorted((root / "outputs").glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict) and {"run_id", "events", "sources"} <= value.keys():
                paths.append(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{path.name}: {exc}")
    return paths, errors


def saved_runs(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        return db.execute("SELECT run_id,status FROM runs WHERE data IS NOT NULL ORDER BY rowid DESC").fetchall()


def database_result(path, run_id):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        row = db.execute("SELECT data FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None or row[0] is None:
        raise ValueError("This run has no saved final result yet")
    return read_result(row[0])


def select_event(event_id):
    st.session_state["selected_event_id"] = event_id


def main(root=ROOT):
    root = Path(root)
    st.set_page_config(page_title="News Engine Events", page_icon="📰", layout="wide")
    st.title("News Engine · Events")
    st.sidebar.header("Saved results")
    if st.sidebar.button("Refresh"):
        st.rerun()
    paths, errors = result_files(root)
    databases = sorted((root / "data").glob("*.sqlite"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for error in errors:
        st.sidebar.warning(error)
    if not paths and not databases:
        st.info("No saved results yet.")
        return
    kinds = (["JSON"] if paths else []) + (["SQLite"] if databases else [])
    kind = st.sidebar.radio("Data source", kinds, key="source_kind")
    try:
        if kind == "JSON":
            name = st.sidebar.selectbox("Result file", [p.name for p in paths], key="json_path")
            result = read_result((root / "outputs" / name).read_text(encoding="utf-8-sig"))
        else:
            name = st.sidebar.selectbox("Database", [p.name for p in databases], key="db_path")
            path = root / "data" / name
            runs = dict(saved_runs(path))
            if not runs:
                st.info("This database has no saved final runs yet.")
                return
            run_id = st.sidebar.selectbox("Run", list(runs), format_func=lambda k: f"{k} · {runs[k]}", key="run_id")
            result = database_result(path, run_id)
    except (OSError, ValueError, sqlite3.Error) as exc:
        st.error(f"Unable to read result: {exc}")
        return

    events = result["events"]
    sources = {a["article_id"]: a for a in result["sources"]}
    st.caption(f"{result['run_id']} · {result.get('status', '')} · {name}")
    columns = st.columns(3)
    for column, label, value in zip(columns, ["Events", "Scored", "Source articles"],
            [len(events), sum(bool(e.get("score")) for e in events), len(sources)]):
        column.metric(label, value)
    st.sidebar.download_button("Download full result JSON", json.dumps(result, ensure_ascii=False, indent=2),
                               file_name=f"{result['run_id']}.json", mime="application/json")
    st.subheader("Run summary")
    st.text(run_overview(events))
    with st.expander("Run metrics"):
        st.json(result.get("metrics", {}))

    query = st.sidebar.text_input("Search Events", key="query").casefold().strip()
    category = st.sidebar.selectbox("Category", ["All", *CATEGORIES.values()], key="category")
    minimum = st.sidebar.slider("Minimum R5", 0, 100, 0, key="min_score")
    unscored = st.sidebar.checkbox("Include unscored Events", value=True, key="unscored")
    filtered = []
    for event in events:
        if category != "All" and CATEGORIES[event["category"]] != category:
            continue
        score = event.get("score")
        text = " ".join(display_text(text) for text in [event["event_id"], event["title"], event.get("summary", ""),
]).casefold()
        if not query or query in text:
            if (score and score["total"] >= minimum) or (not score and unscored):
                filtered.append(event)
    st.subheader(f"Events · {len(filtered)} / {len(events)}")
    by_id = {e["event_id"]: e for e in filtered}
    if st.session_state.get("selected_event_id") not in by_id:
        st.session_state["selected_event_id"] = next(iter(by_id), None)
    selected = st.session_state["selected_event_id"]
    for column, (category, label) in zip(st.columns(3), CATEGORIES.items()):
        members = [e for e in filtered if e["category"] == category]
        with column:
            st.subheader(label)
            st.caption(f"{len(members)} Events")
            with st.container(height=520, border=True):
                if not members:
                    st.caption("No matching Events")
                for member in members:
                    with st.container(border=True):
                        st.text(display_text(member["title"]))
                        score = member.get("score")
                        st.caption(f"R5 {score['total']} · P {score['impact']} / R {score['relevance']} / U {score['urgency']}"
                                   if score else "Unscored")
                        st.button("View details", key=f"view_{member['event_id']}",
                                  type="primary" if member["event_id"] == selected else "secondary",
                                  on_click=select_event, args=(member["event_id"],))
    if not filtered:
        st.info("No Events match the filters.")
        return
    event = by_id[selected]
    st.header(display_text(event["title"]))
    st.caption(f"{selected} · {CATEGORIES.get(event['category'], event['category'])}")
    overview, treasury, articles, raw = st.tabs(["Event summary", "Treasury analysis", "Source articles", "Event JSON"])
    with overview:
        for paragraph in summary_paragraphs(event.get("summary", "")):
            st.text(paragraph)
    with treasury:
        score = event.get("score")
        if score:
            for column, label, key in zip(st.columns(4), ["R5", "Impact · P", "Relevance · R", "Urgency · U"],
                                           ["total", "impact", "relevance", "urgency"]):
                column.metric(label, score[key])
            for key in ("impact", "relevance", "urgency"):
                st.markdown(f"**{key.title()}**")
                st.text(display_text(score.get("reasoning", {}).get(key, "")))
        else:
            st.info("This Event has not been scored yet.")
        for label, field in [("Why Does It Matter?", "why_it_matters"), ("What Should We Do?", "what_to_do")]:
            st.subheader(label)
            for text in event.get(field, []):
                st.text(display_text(text))
    with articles:
        for source_id in event.get("article_ids", []):
            article = sources.get(source_id)
            if not article:
                st.info(f"{source_id}: Original article text is not included in this saved result.")
                continue
            st.subheader(display_text(article["title"]))
            st.caption(f"{source_id} · {article.get('publisher') or ''} · {article.get('published_at') or ''}")
            st.link_button("Open source", article["url"])
            with st.expander("Original article text"):
                st.code(article.get("text", ""), language=None, wrap_lines=True)
    with raw:
        st.json(event)
        st.download_button("Download Event JSON", json.dumps(event, ensure_ascii=False, indent=2),
                           file_name=f"{selected}.json", mime="application/json")


if __name__ == "__main__":
    main()
