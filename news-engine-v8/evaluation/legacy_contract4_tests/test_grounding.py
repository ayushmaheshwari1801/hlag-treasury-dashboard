"""Numerical trust-boundary regressions for extraction and downstream prose."""
from datetime import datetime, timezone
from pathlib import Path
import unittest
from test_mvp import policy_timing, analysis_reply

from news_engine.analyze import AnalysisDraft, analyze_event
from news_engine.clients import _strict_schema
from news_engine.extract import extract_article, _absolute_dates, _numbers, _unit_supported
from news_engine.events import GroupingDraft
from news_engine.models import Article, Event, EventIdentity


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Reply:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def complete(self, model, prompt, context, *, stage):
        self.calls += 1
        return model.model_validate(self.value)


def article():
    return Article(article_id="A1", url="https://example.org/rate", title="Rate decision",
                   text="The bank cut its rate by 25 basis points.", publisher="Example",
                   discovered_at=NOW, normalized_url="https://example.org/rate", content_hash="hash")


def extraction(comparison=None):
    return {"complete": True, "changes": [{"description": "The bank cut its rate by 25 basis points.",
        "direction": "decrease", "claim_type": "actual", "evidence_quote": article().text,
        "numeric": [{"value": "25", "unit": "bps", "kind": "delta", "comparison_basis": comparison,
                     "source_quote": "25 basis points"}]}]}


class GroundingTests(unittest.TestCase):
    def test_original_relative_date_cannot_become_publication_date(self):
        source = Path(__file__).resolve().parents[1] / "evaluation/data/real_workflow/sources/real-a114d8803587.txt"
        source_text = source.read_text(encoding="utf-8")
        quote = "The rule is effective immediately and comments will be accepted for 30 days after publication in the Federal Register ."
        response = {"complete": True, "changes": [{"description": quote,
            "direction": "unknown", "claim_type": "announced", "evidence_quote": quote,
            "occurrence_at": "2020-04-09T00:00:00Z", "numeric": [{"value":"30","unit":"days","kind":"level","source_quote":"30 days"}]}]}
        source_article = article().model_copy(update={"text": source_text,
            "published_at": datetime(2020, 4, 9, tzinfo=timezone.utc)})
        rejected = Reply(response)
        with self.assertRaisesRegex(ValueError, "absolute calendar date"):
            extract_article(source_article, rejected)
        self.assertEqual(rejected.calls, 2)
        class Repaired(Reply):
            def complete(self, model, prompt, context, *, stage):
                self.assert_context = context
                if "repair" in context:
                    self.value["changes"][0]["occurrence_at"] = None
                return super().complete(model, prompt, context, stage=stage)
        repaired = Repaired(response)
        accepted = extract_article(source_article, repaired)
        self.assertIsNone(accepted[0].occurrence_at)
        self.assertNotIn("published_at", repaired.assert_context)
        self.assertEqual(repaired.calls, 2)
        self.assertIn("immediately", accepted[0].description)

    def test_absolute_date_formats_are_mechanically_supported(self):
        expected = datetime(2020, 4, 9).date()
        for text in ("On 2020-04-09", "On April 9, 2020", "On 9 April 2020", "On Apr. 9th, 2020"):
            self.assertIn(expected, _absolute_dates(text))
        self.assertEqual(_absolute_dates("Today, April 9; on 2020-02-30"), set())

    def test_original_planned_facility_qualification_survives_analysis(self):
        source = Path(__file__).resolve().parents[1] / "evaluation/data/real_workflow/sources/real-7645ea4d8dda.txt"
        source_text = source.read_text(encoding="utf-8")
        quote = "the Federal Reserve will establish a facility to provide term financing backed by PPP loans."
        response = {"complete": True, "changes": [{"description": quote,
            "direction": "unknown", "claim_type": "announced", "evidence_quote": quote,
            "occurrence_at": None, "numeric": []}]}
        change = extract_article(article().model_copy(update={"text": source_text}), Reply(response))[0]
        self.assertEqual(change.claim_type, "announced")
        self.assertIsNone(change.occurrence_at)
        event = Event(category='regulatory',event_id="E1", title=change.description, what_changed=[change],
                      latest_change_id=change.change_id, identity=EventIdentity(subject="Federal Reserve", occurrence="Planned PPP financing facility", scope="term financing"),
                      created_at=NOW, last_seen_at=NOW, last_material_at=NOW)
        output = analysis_reply(primary_change_id=change.change_id,
            impact=1,relevance=3,urgency=2,impact_basis='commentary_or_forecast',
            relevance_basis='local_unproven',urgency_basis='routine_or_unknown',has_treasury_action_or_constraint=False,
            why_it_matters=['The planned facility could support eligible lenders, with company applicability unknown.'],
            what_to_do=['If applicable, monitor the planned facility details.'])
        analyzed = analyze_event(event, Reply(output), NOW.isoformat())
        self.assertEqual(analyzed.what_changed[0].claim_type,'announced')
        self.assertEqual(analyzed.what_changed[0].evidence.quote,quote)


    def test_provider_schema_requires_three_named_reason_strings(self):
        schema = _strict_schema(AnalysisDraft.model_json_schema())
        reasons = schema["$defs"]["Reasoning"]
        self.assertEqual(set(reasons["properties"]), {"impact", "relevance", "urgency"})
        self.assertEqual(set(reasons["required"]), {"impact", "relevance", "urgency"})
        self.assertFalse(reasons["additionalProperties"])

    def test_authentic_zero_percent_is_an_exact_numeric_observation(self):
        source = Path(__file__).resolve().parents[1] / "evaluation/data/real_workflow/sources/real-a114d8803587.txt"
        source_text = source.read_text(encoding="utf-8")
        quote = "a zero percent risk weight applies to loans covered by the PPP for capital purposes."
        self.assertIn(quote, source_text)
        source_article = article().model_copy(update={"text": source_text})
        response = {"complete": True, "changes": [{"description": quote,
            "direction": "unknown", "claim_type": "announced", "evidence_quote": quote,
            "numeric": [{"value": "0", "unit": "percent", "kind": "level", "source_quote": "zero percent risk weight"}]}]}
        response["changes"].append(policy_timing(source_text))
        llm = Reply(response)
        changes = extract_article(source_article, llm)
        self.assertEqual(llm.calls, 1)
        self.assertEqual(changes[0].numeric[0].value, "0")
        self.assertEqual(changes[0].evidence.quote, quote)
        self.assertEqual(_numbers("twenty-five; one hundred; twenty five"), set())
        self.assertEqual(_numbers("twenty basis points and five percent"), {20, 5})

    def test_comparison_cannot_introduce_a_missing_baseline(self):
        llm = Reply(extraction("compared with the previous 50 basis points"))
        with self.assertRaisesRegex(ValueError, "comparison_basis"):
            extract_article(article(), llm)
        self.assertEqual(llm.calls, 2)

    def test_amounts_cannot_be_invented_in_analysis(self):
        change = extract_article(article(), Reply(extraction()))[0]
        event = Event(category='regulatory',event_id="E1", title="Rate decision", what_changed=[change],
                      latest_change_id=change.change_id,
                      identity=EventIdentity(subject="The bank", occurrence="Rate decision", scope="policy rate"),
                      created_at=NOW, last_seen_at=NOW, last_material_at=NOW)
        output = analysis_reply(primary_change_id=change.change_id,impact=2,relevance=4,urgency=2,
            impact_basis='benchmark_or_operating',relevance_basis='direct_channel',urgency_basis='routine_or_unknown',
            has_treasury_action_or_constraint=False,why_it_matters=['The bank cut its rate by 50 basis points.'],
            what_to_do=['If applicable, review rate assumptions.'])
        llm = Reply(output)
        with self.assertRaisesRegex(ValueError, 'numerical values'):
            analyze_event(event, llm, NOW.isoformat())
        self.assertEqual(llm.calls, 2)
        output['why_it_matters'] = ['The bank cut its rate by 25 basis points.']
        self.assertEqual(analyze_event(event, Reply(output), NOW.isoformat()).score.total, 54)

    def test_numeric_symbols_and_units_preserve_meaning(self):
        self.assertTrue(_unit_supported("%", "25%"))
        self.assertTrue(_unit_supported("$", "$25 million"))
        self.assertTrue(_unit_supported("million USD", "US$25 million"))
        self.assertFalse(_unit_supported("USD", "$25 million"))
        self.assertFalse(_unit_supported("percent", "25 percentage points"))
        self.assertFalse(_unit_supported("bps", "25 percent"))
        self.assertEqual(_numbers("The change was 25."), {25})


if __name__ == "__main__":
    unittest.main()
