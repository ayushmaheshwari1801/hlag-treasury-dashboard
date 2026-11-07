"""Read-only viewer regressions; optional Streamlit extra, never model calls."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest


HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None
if HAS_STREAMLIT:
    from streamlit.testing.v1 import AppTest
    from streamlit_app import database_result, display_text, run_overview, saved_runs, summary_paragraphs


@unittest.skipUnless(HAS_STREAMLIT, "Install the optional viewer extra")
class StreamlitViewerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        (self.root / "outputs").mkdir()
        (self.root / "data").mkdir()
        self.result = json.loads(Path("examples/event-result.json").read_text(encoding="utf-8-sig"))
        self.result["run_id"] = "R-viewer-latest"
        self.result["status"] = "partial"
        self.result["errors"] = [dict(stage="construct", item_id="A", code="example", message="Fixture partial result")]
        second = deepcopy(self.result["events"][0])
        second.update(event_id="E-viewer-industry", title="Industry fixture", category="industry", score=None)
        self.result["events"].append(second)
        self.latest = self.root / "outputs/latest.json"
        self.latest.write_text(json.dumps(self.result, ensure_ascii=False), encoding="utf-8-sig")
        older = self.root / "outputs/older.json"
        older.write_text(json.dumps({**self.result, "run_id": "R-older"}), encoding="utf-8")
        os.utime(older, (1, 1))
        (self.root / "outputs/summary.json").write_text('{"status":"passed"}', encoding="utf-8")

    def tearDown(self):
        self.folder.cleanup()

    def app(self):
        script = f"from pathlib import Path\nfrom streamlit_app import main\nmain(Path({str(self.root)!r}))"
        return AppTest.from_string(script, default_timeout=20).run()

    def test_latest_snapshot_filters_and_literal_citations(self):
        app = self.app()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="json_path").value, "latest.json")
        self.assertEqual(len([button for button in app.button if button.key and button.key.startswith("view_")]), 2)
        quote = self.result["sources"][0]["text"]
        self.assertIn(quote, [block.value for block in app.code])
        app.button(key="view_E-viewer-industry").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["selected_event_id"], "E-viewer-industry")
        app.selectbox(key="category").set_value("Industry").run()
        self.assertEqual(len([b for b in app.button if b.key and b.key.startswith("view_")]), 1)
        app.selectbox(key="category").set_value("All").run()
        app.text_input(key="query").set_value("Industry fixture").run()
        app.checkbox(key="unscored").uncheck().run()
        self.assertTrue(any("No Events match the filters" in item.value for item in app.info))
        app.text_input(key="query").set_value("").run()
        app.slider(key="min_score").set_value(21).run()
        self.assertTrue(any("No Events match the filters" in item.value for item in app.info))
        app.slider(key="min_score").set_value(0).run()
        app.text_input(key="query").set_value("not in any event").run()
        self.assertTrue(any("No Events match the filters" in item.value for item in app.info))
        self.assertFalse(app.exception)

    def test_three_category_columns_keep_order_and_select_details(self):
        macro = deepcopy(self.result["events"][0])
        macro.update(event_id="E-viewer-macro", title="Macro fixture", category="macroeconomics")
        lower = deepcopy(self.result["events"][0])
        lower.update(event_id="E-viewer-low", title="Lower regulatory fixture")
        lower["score"].update(impact=0, relevance=0, urgency=0, total=0)
        self.result["events"] = [self.result["events"][0], macro, lower, self.result["events"][1]]
        self.latest.write_text(json.dumps(self.result), encoding="utf-8")
        app = self.app()
        self.assertFalse(app.exception)
        headings = [heading.value for heading in app.subheader]
        for label in ("Regulatory", "Macroeconomics", "Industry"):
            self.assertIn(label, headings)
        expected = [event["event_id"] for category in ("regulatory", "macroeconomics", "industry")
                    for event in self.result["events"] if event["category"] == category]
        self.assertEqual([button.key for button in app.button if button.key and button.key.startswith("view_")],
                         [f"view_{event_id}" for event_id in expected])
        self.assertFalse(app.multiselect)
        self.assertIn("Saved results", [heading.value for heading in app.header])
        self.assertIn("Run summary", headings)
        self.assertIn("Refresh", [button.label for button in app.button])
        app.button(key="view_E-viewer-macro").click().run()
        self.assertEqual(app.header[0].value, "Macro fixture")
        app.text_input(key="query").set_value("not present").run()
        self.assertFalse(app.exception)
        for label in ("Regulatory", "Macroeconomics", "Industry"):
            self.assertIn(label, [heading.value for heading in app.subheader])
        self.assertIsNone(app.session_state["selected_event_id"])

    def test_short_overview_and_encoding_repair_leave_evidence_and_files_unchanged(self):
        clean = "Each service saves 7–10 days of transit."
        damaged = clean.encode("utf-8").decode("latin-1")
        self.result["events"][0]["summary"] = damaged
        self.result["overall_summary"] = "Full legacy output.\n" * 10000
        self.result["events"][0]["why_it_matters"] = ["Long analysis " * 1000]
        self.latest.write_text(json.dumps(self.result, ensure_ascii=False), encoding="utf-8-sig")
        before = self.latest.read_bytes()
        quote = self.result["sources"][0]["text"]
        app = self.app()
        self.assertFalse(app.exception)
        self.assertIn(clean, [item.value for item in app.text])
        self.assertIn(quote, [item.value for item in app.code])
        overview = next(item.value for item in app.text if item.value.startswith("2 Events,"))
        self.assertLessEqual(len(overview), 700)
        self.assertNotIn("\n", overview)
        self.assertNotIn("Long analysis", overview)
        self.assertFalse(any(item.label.startswith("Run errors") for item in app.expander))
        self.assertNotIn("Errors", [item.label for item in app.metric])
        self.assertEqual(self.latest.read_bytes(), before)
        for text in ("正常中文与引文", "7–10 days", "café", "ASCII"):
            self.assertEqual(display_text(text), text)
        self.assertEqual(display_text(clean.encode("utf-8").decode("cp1252")), clean)
        self.assertEqual(run_overview([]), "No saved Events for this run.")

    def test_summary_paragraphs_preserve_text_and_abbreviations(self):
        paragraphs = ["The U.S. Treasury announced the measure.", "Dr. Smith noted the uncertainty."]
        self.assertEqual(summary_paragraphs("\n\n".join(paragraphs)), paragraphs)
        sentences = ["The U.S. Treasury announced a measure that may affect financing conditions. " * 3,
                     "Dr. Smith noted that company exposure remains uncertain. " * 3,
                     "Rates moved by 2.5 percent while monitoring continued. " * 3]
        block = "".join(sentences).strip()
        parts = summary_paragraphs(block)
        self.assertGreater(len(parts), 1)
        self.assertEqual(" ".join(parts), block)
        self.assertFalse(any(part.endswith(("U.S.", "Dr.")) for part in parts))
        self.result["events"][0]["summary"] = "\n\n".join(paragraphs)
        self.latest.write_text(json.dumps(self.result), encoding="utf-8")
        before = self.latest.read_bytes()
        app = self.app()
        self.assertFalse(app.exception)
        for paragraph in paragraphs:
            self.assertIn(paragraph, [item.value for item in app.text])
        self.assertEqual(self.latest.read_bytes(), before)

    def test_sqlite_saved_snapshot_is_read_only_and_hides_running_rows(self):
        path = self.root / "data/results.sqlite"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE runs(run_id TEXT,status TEXT,data TEXT)")
            db.execute("INSERT INTO runs VALUES(?,?,?)", (self.result["run_id"], "partial", json.dumps(self.result)))
            db.execute("INSERT INTO runs VALUES('R-running','running',NULL)")
        db.close()
        before = path.read_bytes()
        self.assertEqual(saved_runs(path), [(self.result["run_id"], "partial")])
        self.assertEqual(database_result(path, self.result["run_id"]), self.result)
        with self.assertRaisesRegex(ValueError, "no saved final result"):
            database_result(path, "R-running")
        app = self.app()
        app.radio(key="source_kind").set_value("SQLite").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="run_id").value, self.result["run_id"])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(Path(str(path) + ".lock").exists())

    def test_empty_and_invalid_json_are_visible_without_fabricated_events(self):
        self.latest.write_text('{broken', encoding="utf-8")
        app = self.app()
        self.assertFalse(app.exception)
        self.assertTrue(app.warning)
        self.assertEqual(app.selectbox(key="json_path").value, "older.json")
        with tempfile.TemporaryDirectory() as empty:
            script = f"from pathlib import Path\nfrom streamlit_app import main\nmain(Path({empty!r}))"
            app = AppTest.from_string(script).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("No saved results yet" in item.value for item in app.info))
