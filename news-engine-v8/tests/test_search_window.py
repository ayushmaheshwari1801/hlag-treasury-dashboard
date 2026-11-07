import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from news_engine.search import SearchQuery, search


class SearchWindowTests(unittest.TestCase):
    def test_new_old_and_recent_watermarks_stay_within_two_days(self):
        now = datetime(2026, 11, 1, tzinfo=timezone.utc)
        for age, expected in ((None, 2), (10, 2), (0, 1)):
            with self.subTest(age=age):
                calls = []
                store = SimpleNamespace(
                    history=lambda: [],
                    search_since=lambda q: None if age is None else (now-timedelta(days=age)).isoformat(),
                    record_search=lambda *args: None,
                )
                llm = SimpleNamespace(complete=lambda *a, **kw: SimpleNamespace(
                    queries=[SearchQuery(query='shipping news', topic='industry', geography='Global')]))
                def discover(query, *, start, end):
                    calls.append((start, end))
                    return {'results': []}
                articles, errors = search(llm, SimpleNamespace(search=discover), store, now=now)
                self.assertEqual(errors, [])
                self.assertEqual(calls, [(now-timedelta(days=expected), now)])
