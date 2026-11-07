"""Existing tests were written against the mock RiskEngine and use their own fixture market databases, so they run
with CASH_ENGINE=mock. Tests marked spec_engine run on hlag_engine."""
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'spec_engine: run with CASH_ENGINE=spec (hlag_engine)')


@pytest.fixture(autouse=True)
def _cash_engine(request, monkeypatch):
    monkeypatch.setenv('CASH_ENGINE', 'spec' if request.node.get_closest_marker('spec_engine') else 'mock')
