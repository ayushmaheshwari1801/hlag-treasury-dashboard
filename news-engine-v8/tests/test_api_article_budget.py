"""The user-facing run API defaults to ten selected Articles."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fastapi.testclient import TestClient
from news_engine.api import create_app, RunRequest
from evaluation.offline_classification import ScriptedLabels

class ArticleBudgetAPITests(unittest.TestCase):
    def test_default_request_caps_processing_and_retains_backlog(self):
        with TemporaryDirectory() as folder:
            app=create_app(database=Path(folder)/'news.sqlite',token='offline',llm=ScriptedLabels())
            values=[dict(url=f'https://example.test/{i}',title=f'Issue {i} | report',text=f'Original source {i}.') for i in range(12)]
            with TestClient(app) as client:
                response=client.post('/api/news/runs',headers={'Authorization':'Bearer offline'},json={'articles':values})
                self.assertEqual(response.status_code,200,response.text)
                result=response.json()
                self.assertEqual(result['metrics']['max_articles'],10)
                self.assertEqual(result['metrics']['articles_selected'],10)
                self.assertEqual(result['metrics']['articles_deferred'],2)
                resumed=client.post('/api/news/runs',headers={'Authorization':'Bearer offline'},json={'articles':[]})
                self.assertEqual(resumed.status_code,200,resumed.text)
                self.assertEqual(resumed.json()['metrics']['articles_selected'],2)
                self.assertEqual(resumed.json()['metrics']['articles_deferred'],0)
    def test_bad_limits_rejected_before_provider_calls(self):
        for value in [0,-1,True,1.5,'10',None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                RunRequest(max_articles=value)
        self.assertEqual(RunRequest().max_articles,10)
