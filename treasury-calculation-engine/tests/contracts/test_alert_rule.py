"""AlertRule contract (4.5)."""

import pytest
from pydantic import ValidationError

from hlag_engine.contracts import AlertRule


def rule(**overrides):
    data = {
        "rule_id": "freight.route_move.wci_sha_rtm", "module": "freight", "series": ["WCI_SHA_RTM"],
        "type": "pct_move", "window": "1w", "unit": "pct", "lookback": 26,
        "thresholds": {"warning": {"value": 10, "vol_multiple": 2.5}, "severe": {"value": 20, "vol_multiple": 4}},
        "on_severe": "request_auto_build",
    }
    data.update(overrides)
    return AlertRule(**data)


def test_valid_rule():
    r = rule()
    assert r.thresholds.severe.vol_multiple == 4 and r.on_severe.value == "request_auto_build"


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "big_move"},
        {"lookback": None},  # vol multiple needs a lookback
        {"type": "spread_change"},  # needs two series
        {"series": []},
        {"thresholds": {"warning": {"value": 20}, "severe": {"value": 10}}},
        {"thresholds": {"warning": {}, "severe": {"value": 10}}},
        {"module": "liquidity"},
        {"rule_id": "Freight Route"},
    ],
)
def test_invalid_rules(overrides):
    with pytest.raises(ValidationError):
        rule(**overrides)
