"""Contract-2 factual projection, centralized R5 caps and deterministic ranking."""
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from pydantic import ValidationError
from news_engine.models import (Event,Score,RunResult,validate_r5,event_rank_key,
    IMPACT_CAPS,RELEVANCE_CAPS,URGENCY_CAPS)
from news_engine.analyze import AnalysisDraft,analyze_event
from news_engine.extract import extract_article
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store
from test_mvp import RecordedLLM,article,analysis_reply,NOW


class R5Tests(unittest.TestCase):
    def event(self):
        change=extract_article(article(),RecordedLLM())[0]
        return Event(category='regulatory',event_id='E1',title='Decision',identity=change.identity,what_changed=[change],latest_change_id=change.change_id,created_at=NOW,last_seen_at=NOW,last_material_at=NOW)

    def test_integer_dimensions_cross_caps_and_arithmetic(self):
        self.assertEqual(validate_r5(3,4,3),67)
        self.assertEqual(validate_r5(5,5,5,total=100),100)
        self.assertEqual(validate_r5(0,0,0),0)
        for values in ((1,0,0),(0,0,1),(2,1,1),(1,1,2),(2,2,3),(-1,4,2),(6,4,2),(True,4,2),(2.5,4,2)):
            with self.subTest(values=values),self.assertRaises(ValueError):
                validate_r5(*values)
        with self.assertRaises(ValueError):validate_r5(3,4,3,total=66)
        with self.assertRaises(ValueError):validate_r5(3,4,3,total=67.0)
        with self.assertRaises(ValidationError):Score(impact=1,relevance=1,urgency=2,total=25,reasoning={})

    def test_all_rubric_basis_ceilings_and_qualifications(self):
        for parameter,caps in (('impact_basis',IMPACT_CAPS),('relevance_basis',RELEVANCE_CAPS),('urgency_basis',URGENCY_CAPS)):
            for basis,ceiling in caps.items():
                # Keep unrelated dimensions free of cross-cap violations.
                values={'impact':0,'relevance':5,'urgency':0}
                dimension=parameter.removesuffix('_basis')
                values[dimension]=ceiling
                self.assertIsInstance(validate_r5(**values,**{parameter:basis}),int)
                if ceiling<5:
                    values[dimension]=ceiling+1
                    with self.subTest(parameter=parameter,basis=basis),self.assertRaisesRegex(ValueError,'anchor/cap'):
                        validate_r5(**values,**{parameter:basis})
        with self.assertRaisesRegex(ValueError,'proposed'):validate_r5(4,4,2,claim_type='proposed')
        with self.assertRaisesRegex(ValueError,'forecast'):validate_r5(2,4,2,claim_type='forecast')
        with self.assertRaisesRegex(ValueError,'concrete Treasury'):validate_r5(3,4,3,has_treasury_action_or_constraint=False)
        self.assertEqual(validate_r5(3,4,3,has_treasury_action_or_constraint=True),67)
        with self.assertRaisesRegex(ValueError,'anchor/cap'):validate_r5(3,4,4,urgency_basis='routine_or_unknown')

    def test_caps_use_one_repair_and_never_silent_clamping(self):
        event=self.event()
        invalid=analysis_reply(primary_change_id=event.latest_change_id,impact=1,relevance=1,urgency=2,
            impact_basis='commentary_or_forecast',relevance_basis='speculative',urgency_basis='routine_or_unknown',has_treasury_action_or_constraint=False)
        valid={**invalid,'urgency':1,'reasoning':{**invalid['reasoning'],'urgency':'The source establishes no company deadline.'}}
        llm=RecordedLLM(lambda stage,ctx:valid if 'repair' in ctx else invalid)
        result=analyze_event(event,llm,NOW.isoformat())
        self.assertEqual(len(llm.calls),2)
        self.assertIn('R=1',llm.calls[1][1]['repair'])
        self.assertEqual(result.score.total,20)
        self.assertEqual(result.score.reasoning,valid['reasoning'])
        failed=RecordedLLM(lambda stage,ctx:invalid)
        with self.assertRaisesRegex(ValueError,'after one repair'):analyze_event(event,failed,NOW.isoformat())
        self.assertEqual(len(failed.calls),2)

    def test_invalid_anchor_id_and_basis_fail_closed(self):
        event=self.event()
        for updates in ({'primary_change_id':'invented'}, {'impact':3,'impact_basis':'benchmark_or_operating'}):
            llm=RecordedLLM(lambda stage,ctx:analysis_reply(ctx,**updates))
            with self.assertRaises(ValueError):analyze_event(event,llm,NOW.isoformat())
            self.assertEqual(len(llm.calls),2)

    def test_storage_refuses_bypassed_invalid_score_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder)/'news.sqlite') as store:
                event=self.event()
                store.save_event(event)
                invalid=Score.model_construct(impact=1,relevance=1,urgency=2,total=25,reasoning={})
                with self.assertRaises(ValidationError):store.save_event(event.model_copy(update={'score':invalid}))
                self.assertIsNone(store.events()[0].score)

    def test_order_is_total_relevance_impact_urgency_time_then_id(self):
        event=self.event()
        def scored(identifier,p,r,u,age=0):
            score=Score(impact=p,relevance=r,urgency=u,total=validate_r5(p,r,u),reasoning={})
            return event.model_copy(update={'event_id':identifier,'score':score,'last_material_at':NOW+timedelta(days=age)})
        items=[scored('Z',2,2,2),scored('B',0,5,1),scored('A',0,5,1),scored('NEW',0,5,1,1),scored('HIGH',3,4,3),event.model_copy(update={'event_id':'UNSCORED'})]
        self.assertEqual([e.event_id for e in sorted(items,key=event_rank_key)],['HIGH','NEW','A','B','Z','UNSCORED'])
        self.assertEqual(event_rank_key(items[0])[:4],(-40,-2,-2,-2))


class OutputContractTests(unittest.TestCase):
    def test_analysis_cannot_generate_or_overwrite_what_changed(self):
        def handler(stage,ctx):
            if stage=='analyze':return {**analysis_reply(ctx),'what_changed':[]}
        with tempfile.TemporaryDirectory() as folder:
            with Store(Path(folder)/'news.sqlite') as store:
                result=run_pipeline(store,RecordedLLM(handler),[{'url':'https://example.org/a','title':'Rule','text':'The rule changed.'}])
                self.assertEqual(result.status,'partial')
                event=result.events[0]
                self.assertEqual(event.what_changed[0].description,'The rule changed.')
                self.assertIsNone(event.score)
                self.assertEqual(event.why_it_matters,[])
                self.assertEqual(event.what_to_do,[])
                self.assertEqual(store.extraction_status(event.what_changed[0].source_id),'accepted')
                self.assertEqual(result.schema_version,4)
                payload=event.model_dump()
                self.assertFalse({'summary','major_changes','treasury_implications','recommendations'} & payload.keys())

    def test_contract_bounds_and_no_old_runtime_database(self):
        with self.assertRaises(ValidationError):RunResult(run_id='R',status='completed',schema_version=1)
        change=extract_article(article(),RecordedLLM())[0]
        response=analysis_reply(primary_change_id=change.change_id,why_it_matters=['a','b','c'])
        with self.assertRaises(ValidationError):AnalysisDraft.model_validate(response)
        with tempfile.TemporaryDirectory() as folder:
            import sqlite3
            path=Path(folder)/'v1.sqlite'
            db=sqlite3.connect(path)
            db.execute('create table metadata(key text primary key,value text)')
            db.execute("insert into metadata values('schema','news-engine-mvp-1')")
            db.commit();db.close()
            before=path.read_bytes()
            with self.assertRaisesRegex(ValueError,'fresh database'):Store(path)
            self.assertEqual(path.read_bytes(),before)
