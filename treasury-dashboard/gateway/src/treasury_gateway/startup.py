"""Prepare every current news Event using the dashboard's own cash calculation."""
from .forecast import ForecastRequest


def prepare_event_impacts(agent, dashboard):
    baseline = dashboard()
    finance = baseline['finance']
    day = str(finance['as_of'])[:10]
    horizon = min(365, finance['horizon_days'])
    pool, _ = agent.reader.cumulative()
    events = (pool or {}).get('events', [])
    saved = agent.forecasts()
    for index, event in enumerate(events, 1):
        print(f'Preparing news cash impact {index}/{len(events)}...', flush=True)
        forecast = next((f for f in saved
            if f.get('selected_event_ids', f.get('event_ids')) == [event['event_id']]
            and f['as_of'] == day and f['horizon_days'] == horizon
            and f.get('source_run_id') == pool['run_id'] and f['mode'] == 'model'), None)
        if forecast is None:
            forecast = agent.predict(ForecastRequest(event_ids=[event['event_id']], as_of=day,
                                                      horizon_days=horizon, mode='model'))
        result = dashboard(forecast)
        if not result['finance'].get('calculation', {}).get('run_id'):
            raise RuntimeError('A news cash-impact calculation did not complete.')
    print(f'All {len(events)} news cash-impact assessments are ready.', flush=True)

