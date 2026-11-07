import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import run_dashboard as launcher


@pytest.mark.parametrize('status', ['completed', 'partial', 'failed'])
def test_startup_news_must_complete_and_always_closes_clients(monkeypatch, tmp_path, status):
    from news_engine import clients, config, pipeline, storage
    monkeypatch.setenv('NEWS_V8_DATABASE', str(tmp_path / 'news.sqlite'))
    monkeypatch.setattr(config, 'load_credentials', lambda *a, **k: SimpleNamespace(
        llm_api_key='test', llm_model='test', llm_base_url='test', exa_api_key='test'))
    llm, exa = Mock(), Mock()
    monkeypatch.setattr(clients, 'LLMClient', lambda *a: llm)
    monkeypatch.setattr(clients, 'ExaClient', lambda *a: exa)
    store = Mock()
    store.__enter__ = Mock(return_value=store)
    store.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(storage, 'Store', lambda *a: store)
    result = SimpleNamespace(status=status, events=[{}], model_dump=lambda **k: {'status':status})
    run = Mock(return_value=result)
    monkeypatch.setattr(pipeline, 'run_pipeline', run)
    reader = Mock()
    if status == 'completed':
        launcher.prepare_news(reader)
    else:
        with pytest.raises(RuntimeError, match='has not opened'):
            launcher.prepare_news(reader)
    run.assert_called_once()
    reader.import_result.assert_called_once_with({'status':status})
    llm.close.assert_called_once()
    exa.close.assert_called_once()


def test_browser_opens_only_after_server_ready(monkeypatch):
    order=[]
    async def started(self, sockets=None):
        order.append('ready')
        self.started=True
    monkeypatch.setattr(launcher.uvicorn.Server, 'startup', started)
    monkeypatch.setattr(launcher.webbrowser, 'open', lambda url: order.append(url))
    server=launcher.DashboardServer(launcher.uvicorn.Config('unused', port=8080))
    asyncio.run(server.startup())
    assert order==['ready','http://127.0.0.1:8080/']


def test_failed_preparation_never_starts_server(monkeypatch):
    # main supplies local service defaults; do not leak those paths into later tests.
    monkeypatch.setattr(launcher.os, 'environ', launcher.os.environ.copy())
    monkeypatch.setattr(launcher.sys, 'argv', ['run_dashboard.py'])
    monkeypatch.setattr(launcher, 'prepare_news', Mock(side_effect=RuntimeError('failure')))
    server=Mock()
    monkeypatch.setattr(launcher, 'DashboardServer', server)
    with pytest.raises(SystemExit) as exc:
        launcher.main()
    assert exc.value.code==1
    server.assert_not_called()
