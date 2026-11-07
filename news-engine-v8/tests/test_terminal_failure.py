import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from news_engine.pipeline import run_pipeline
from news_engine.storage import Store


class TerminalFailureTests(unittest.TestCase):
    def test_unexpected_failure_is_saved_without_retry_or_sensitive_payload(self):
        with tempfile.TemporaryDirectory() as directory, Store(Path(directory) / 'news.sqlite') as store:
            def fail(active_store, *args):
                active_store.start_run('Rfailure')
                active_store.progress('Rfailure', 'consolidation')
                raise RuntimeError('private provider response must not be displayed')
            with patch('news_engine.pipeline._run', side_effect=fail) as run:
                result = run_pipeline(store, object())
            self.assertEqual(run.call_count, 1)
            self.assertEqual(result.status, 'failed')
            self.assertEqual(result.errors[0].stage, 'consolidation')
            self.assertEqual(result.errors[0].code, 'RuntimeError')
            self.assertNotIn('private provider', result.model_dump_json())
            self.assertEqual(store.get_run('Rfailure').status, 'failed')
