"""Prove W4 worker overlap and caller-owned SQLite writes without paid calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Lock, get_ident
import unittest
from unittest.mock import patch

from evaluation.offline_classification import ScriptedLabels
from news_engine.pipeline import run_pipeline
from news_engine.storage import Store


class ParallelAnalysisTests(unittest.TestCase):
    def test_bounded_parallel_analysis_and_main_thread_persistence(self):
        class OverlappingLabels(ScriptedLabels):
            def __init__(self):
                super().__init__()
                self.barrier=Barrier(2)
                self.lock=Lock()
                self.active=0
                self.peak=0
                self.analysis_threads=set()

            def complete(self,schema,prompt,context,*,stage):
                if stage!='analyze_batch':
                    return super().complete(schema,prompt,context,stage=stage)
                with self.lock:
                    self.active+=1
                    self.peak=max(self.peak,self.active)
                    self.analysis_threads.add(get_ident())
                try:
                    # A serial executor cannot release either pair of requests.
                    self.barrier.wait(timeout=5)
                    return super().complete(schema,prompt,context,stage=stage)
                finally:
                    with self.lock:
                        self.active-=1

        articles=[dict(url='https://example.test/'+issue,title=issue,
            text=issue+' independent policy financing announcement.',
            published_at='2026-01-01T00:00:00Z')
            for issue in ['Alpha','Bravo','Charlie','Delta']]
        llm=OverlappingLabels()
        caller=get_ident()
        writes=[]
        with TemporaryDirectory() as folder, Store(Path(folder)/'v8.sqlite') as store:
            original_save=store.save_event
            def save(event):
                writes.append(get_ident())
                self.assertEqual(get_ident(),caller)
                return original_save(event)
            with patch.object(store,'save_event',side_effect=save):
                result=run_pipeline(store,llm,articles=articles,
                    analysis_batch_size=1,analysis_workers=2)
            self.assertEqual(result.status,'completed',result.errors)
            self.assertEqual(len(result.events),4)
            self.assertTrue(all(event.score is not None for event in result.events))
            self.assertEqual(llm.peak,2)
            self.assertEqual(len(llm.analysis_threads),2)
            self.assertNotIn(caller,llm.analysis_threads)
            self.assertEqual(writes,[caller]*4)
            self.assertEqual(result.metrics['analysis_batches'],4)
            self.assertEqual(result.metrics['analysis_workers'],2)


if __name__=='__main__':
    unittest.main()
