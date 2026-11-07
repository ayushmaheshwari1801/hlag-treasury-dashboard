from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from treasury_gateway.startup import prepare_event_impacts


def fixture():
    saved = {'selected_event_ids':['E1'], 'as_of':'2026-11-07', 'horizon_days':180,
             'source_run_id':'pool_current', 'mode':'model', 'forecast_id':'saved'}
    agent = SimpleNamespace(reader=SimpleNamespace(cumulative=lambda: ({'run_id':'pool_current',
        'events':[{'event_id':'E1'},{'event_id':'E2'}]}, None)), forecasts=lambda:[saved],
        predict=Mock(return_value={'forecast_id':'new'}))
    calculate=Mock(return_value={'finance':{'as_of':'2026-11-07', 'horizon_days':180,
                                           'calculation':{'run_id':'cash'}}})
    return agent,calculate,saved


def test_every_event_calculated_and_matching_forecast_reused():
    agent,calculate,saved=fixture()
    prepare_event_impacts(agent,calculate)
    assert calculate.call_count==3
    assert calculate.call_args_list[1].args==(saved,)
    agent.predict.assert_called_once()
    assert agent.predict.call_args.args[0].event_ids==['E2']


def test_old_snapshot_forecast_not_reused():
    agent,calculate,saved=fixture()
    saved['source_run_id']='old'
    prepare_event_impacts(agent,calculate)
    assert agent.predict.call_count==2


def test_unfinished_calculation_blocks_readiness():
    agent,calculate,_=fixture()
    calculate.side_effect=[calculate.return_value, {'finance':{}}]
    with pytest.raises(RuntimeError,match='did not complete'):
        prepare_event_impacts(agent,calculate)
    agent.predict.assert_not_called()
