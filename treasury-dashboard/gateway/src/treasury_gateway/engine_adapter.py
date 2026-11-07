"""Run hlag_engine (treasury-calculation-engine) behind the gateway's RiskEngine interface.

Selected with CASH_ENGINE=spec. The adapter is read-only: the market store is opened with SQLite's
mode=ro and runs are never written to the engine store (run_scenario(..., write=False)). The engine's
behaviour is unchanged; this module only converts inputs and outputs.

Conventions
- Dashboard scenarios use fractions (0.1 = 10%); hlag_engine uses whole percents (10 = 10%).
  Shares stay fractions in both; basis points are basis points in both.
- hlag_engine amounts are full currency units (Decimal); the dashboard result uses millions (float).
- result['scenario'] echoes the normalised dashboard-convention scenario so the page's sliders read back
  correctly; result['engine_params'] holds the ScenarioParams actually run (whole percents).
"""
from __future__ import annotations

import copy
import hashlib
import logging
import sqlite3
import threading
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hlag_engine.company_profile.registers import load_registers
from hlag_engine.company_profile.resolve import RunContext
from hlag_engine.company_profile.scenario_validation import load_scenario_ranges
from hlag_engine.company_profile.schema import Profile, load_profile
from hlag_engine.contracts.adjustment import Adjustment
from hlag_engine.contracts.enums import PassType, PresetId, ScenarioSource
from hlag_engine.contracts.scenario import ScenarioParams
from hlag_engine.data.series import SERIES, SourceKind
from hlag_engine.data.store import Store, StoreError, utc_now
from hlag_engine.engine.compare import apply_state_change, difference
from hlag_engine.engine.liquidity import TreatmentFlags, line_treatment
from hlag_engine.engine.presets import LIBRARY, build_preset
from hlag_engine.engine.record import canonical
from hlag_engine.engine.run import DEFAULT_STORE_PATH, Engine, RunResult
from hlag_engine.engine.snapshot import default_as_of
from hlag_engine.modules.freight.calc import INDEX_SERIES
from hlag_engine.modules.interface import ProfileContext

from .risk_engine import TRADES, scenario_params as normalise

log = logging.getLogger(__name__)
M = Decimal(1_000_000)
DRIVERS = ('freight', 'bunker', 'rates', 'fx')
UNITS = 'USD millions; native line amounts in currency millions'
# Licence lines (spec 3.6). Agent series use the Bloomberg wording of pending decision P91.
LICENCE = {
    SourceKind.API: 'public reference data, cited.',
    SourceKind.AGENT: ('WCI: Drewry World Container Index via Bloomberg Terminal; fuel: ClearLynx bunker assessments '
                       'via Bloomberg Terminal. Licensed data (university licence), internal non-commercial coursework '
                       'use only; not for redistribution. (Wording pending decision P91.)'),
}


def default_store_path() -> Path:
    import os
    return Path(os.environ.get('ENGINE_MARKET_DB') or DEFAULT_STORE_PATH)


# ---------------------------------------------------------------- read-only store and shared engine


class ReadOnlyStore(Store):
    """hlag_engine's Store on a read-only SQLite connection. Skips Store.__init__ (which creates and migrates
    tables) and refuses every write."""

    def __init__(self, path: Path | str):
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(f'hlag_engine market store not found: {path}')
        self.path = str(path)
        self.clock = utc_now
        self._depth = 0
        self.conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def transaction(self):  # every Store write goes through a transaction
        raise StoreError('the gateway opens the hlag_engine store read-only')


class _GatewayEngine(Engine):
    """Engine whose approved adjustments come from the gateway (converted to our Adjustment contract) instead of
    the engine store's adjustments table. Nothing else differs."""

    gateway_adjustments: list[Adjustment] = []

    def _adjustments(self, params: ScenarioParams) -> list[Adjustment]:
        return list(self.gateway_adjustments)


class _Shared:
    """One store, engine and profile per store path; the engine is not thread-safe, so runs take the lock."""

    _instances: dict[str, '_Shared'] = {}
    _guard = threading.Lock()

    def __init__(self, path: Path):
        self.lock = threading.RLock()
        self.store = ReadOnlyStore(path)
        self.profile: Profile = load_profile()
        self.registers = load_registers()
        self.engine = _GatewayEngine(self.store, self.profile, self.registers)

    @classmethod
    def get(cls, path: Path | str | None = None) -> '_Shared':
        key = str(Path(path or default_store_path()).resolve())
        with cls._guard:
            if key not in cls._instances:
                cls._instances[key] = cls(Path(key))
            return cls._instances[key]


# ---------------------------------------------------------------- input conversion


def _pct(v):
    return round(v * 100, 10)


def _frac(v):
    return round(v / 100, 12)


def to_engine_params(dash: dict, as_of: date) -> ScenarioParams:
    """A normalised dashboard scenario (risk_engine.scenario_params output) as hlag_engine ScenarioParams."""
    f, b, r, x = dash.get('freight', {}), dash.get('bunker', {}), dash.get('rates', {}), dash.get('fx', {})
    freight = {k: {t: _pct(v) for t, v in f[k].items()} for k in ('rate_shock_pct', 'volume_shock_pct') if k in f}
    if 'transit_delay_days' in f:
        freight['transit_delay_days'] = {t: int(round(v)) for t, v in f['transit_delay_days'].items()}
    if 'shape' in f:
        freight['shape'] = f['shape']
    bunker = {k: _pct(b[k]) for k in ('vlsfo_shock_pct', 'hsfo_shock_pct', 'consumption_uplift_pct') if k in b}
    if 'uplift_affected_share' in b:
        bunker['uplift_affected_share'] = b['uplift_affected_share']
    elif 'consumption_uplift_pct' in b:
        bunker['uplift_affected_share'] = 1.0  # the dashboard engine's default when the share is omitted
    if 'shape' in b:
        bunker['shape'] = b['shape']
    rates = {k: r[k] for k in ('credit_spread_change_bp', 'refinancing_mode', 'zim_tranche_on') if k in r}
    if r.get('rate_path'):
        rates['rate_path'] = {c: [{'date': s['date'], 'step_bp': s['step_bp']} for s in steps]
                              for c, steps in r['rate_path'].items()}
    fx = {}
    if x.get('move_pct'):
        fx['move_pct'] = {c: _pct(v) for c, v in x['move_pct'].items()}
    shape = x.get('shape')
    if isinstance(shape, str):
        fx['shape'] = {c: shape for c in x.get('move_pct', {})}
    elif shape:
        fx['shape'] = dict(shape)
    hedges = [{'module': h['module'], 'instrument': h['instrument'], 'target': h['target'],
               'coverage_pct': _pct(h['coverage_pct']), 'tenor_months': h['tenor_months'],
               'strike_rule': h.get('strike_rule', 'spot_at_T0'), 'start_date': h.get('start_date', as_of.isoformat())}
              for h in dash.get('hedge_tests', [])]
    preset_id = dash.get('preset_id')
    if preset_id not in {p.value for p in PresetId}:
        preset_id = None  # e.g. the dashboard's 'baseline' preset
    source = dash.get('source', 'custom')
    if source == 'preset' and preset_id is None:
        source = 'custom'
    raw = {'name': dash.get('name') or 'Baseline', 'start_date': dash['start_date'],
           'duration_days': dash.get('duration_days'), 'default_shape': dash.get('default_shape', 'step'),
           'ramp_or_revert_weeks': dash.get('ramp_or_revert_weeks'), 'source': source,
           'event_ids': list(dash.get('event_ids', [])), 'preset_id': preset_id,
           'freight': freight, 'bunker': bunker, 'rates': rates, 'fx': fx,
           # direct cash flows use the engine's convention unchanged: dated signed USD million amounts
           'direct': {'cash_flows': [dict(f) for f in (dash.get('direct') or {}).get('cash_flows', [])]},
           'hedge_tests': hedges, 'overrides': dict(dash.get('overrides', {}))}
    # JSON validation may produce floats for whole-number calendar/count inputs.
    whole_numbers = {
        'fx.obligation_shift_months': 'Payment shift',
        'rates.floating_tranches_per_currency': 'Floating tranches per currency',
        'rates.deposit_cohorts_per_currency': 'Deposit cohorts per currency',
    }
    for key, label in whole_numbers.items():
        if key in raw['overrides']:
            value = raw['overrides'][key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not float(value).is_integer():
                raise ValueError(f'{label} must be a whole number')
            raw['overrides'][key] = int(value)
    try:
        return ScenarioParams.model_validate(raw)
    except ValueError as exc:
        # A preset scenario edited with other drivers (e.g. a news signal merged into it) is "based on" the preset.
        if source == 'preset' and 'is scoped to' in str(exc):
            return ScenarioParams.model_validate({**raw, 'source': 'custom'})
        raise


def to_dashboard_scenario(params: ScenarioParams) -> dict:
    """hlag_engine ScenarioParams in the dashboard convention (used for presets)."""
    p = params
    out = {'name': p.name, 'source': p.source.value, 'preset_id': p.preset_id.value if p.preset_id else None,
           'event_ids': list(p.event_ids), 'start_date': p.start_date.isoformat(), 'duration_days': p.duration_days,
           'default_shape': p.default_shape.value,
           'ramp_or_revert_weeks': p.ramp_or_revert_weeks if p.ramp_or_revert_weeks else 8,
           'freight': {}, 'bunker': {}, 'rates': {}, 'fx': {}}
    for k in ('rate_shock_pct', 'volume_shock_pct'):
        if getattr(p.freight, k):
            out['freight'][k] = {t.value: _frac(v) for t, v in getattr(p.freight, k).items()}
    if p.freight.transit_delay_days:
        out['freight']['transit_delay_days'] = {t.value: v for t, v in p.freight.transit_delay_days.items()}
    if p.freight.shape:
        out['freight']['shape'] = p.freight.shape.value
    for k in ('vlsfo_shock_pct', 'hsfo_shock_pct', 'consumption_uplift_pct'):
        if getattr(p.bunker, k):
            out['bunker'][k] = _frac(getattr(p.bunker, k))
    if p.bunker.consumption_uplift_pct:
        out['bunker']['uplift_affected_share'] = p.bunker.uplift_affected_share
    if p.bunker.shape:
        out['bunker']['shape'] = p.bunker.shape.value
    if p.rates.rate_path:
        out['rates']['rate_path'] = {c.value: [{'date': s.date.isoformat(), 'step_bp': s.step_bp} for s in steps]
                                     for c, steps in p.rates.rate_path.items()}
    if p.rates.credit_spread_change_bp:
        out['rates']['credit_spread_change_bp'] = p.rates.credit_spread_change_bp
    if p.rates.refinancing_mode.value != 'rollover':
        out['rates']['refinancing_mode'] = p.rates.refinancing_mode.value
    if p.fx.move_pct:
        out['fx']['move_pct'] = {c.value: _frac(v) for c, v in p.fx.move_pct.items()}
    if p.fx.shape:
        out['fx']['shape'] = {c.value: s.value for c, s in p.fx.shape.items()}
    if p.direct.cash_flows:
        out['direct'] = {'cash_flows': [f.model_dump(mode='json') for f in p.direct.cash_flows]}
    return out


def to_engine_adjustments(items: list[dict] | None, as_of: date) -> list[Adjustment]:
    """Gateway-approved adjustments as our Adjustment contract. The gateway catalogue keys are our profile keys
    in our units. The gateway does not carry a confidence; it is set to 0 (not used in calculations)."""
    out = []
    for a in items or []:
        if a.get('status') not in {'approved', None}:
            continue
        if a.get('review_date') and str(a['review_date']) <= as_of.isoformat():
            continue
        out.append(Adjustment(
            adjustment_id=a.get('adjustment_id') or a.get('id'), param_key=a['param_key'],
            old_value=a.get('old_value'), new_value=a['new_value'], step=a.get('step'),
            effective_date=a.get('effective_date', as_of.isoformat()), review_date=a['review_date'],
            event_ids=list(a.get('event_ids', [])), justification=a.get('justification') or 'gateway adjustment',
            confidence=a.get('confidence', 0.0), status='approved', approver=a.get('approver') or 'gateway',
            decided_at=a.get('decided_at') or utc_now().isoformat(), proposed_by=a.get('proposed_by') or 'gateway'))
    return out


# ---------------------------------------------------------------- profile in the dashboard shape


def _source_text(entry) -> str | None:
    if entry.source is None:
        return None
    return entry.source.report + (f' p{entry.source.page}' if entry.source.page else '')


def _entry(profile: Profile, key: str, scale: float = 1.0, formula: str | None = None) -> dict:
    e = profile.parameters[key]
    value = e.value / scale if isinstance(e.value, (int, float)) and not isinstance(e.value, bool) and scale != 1 else e.value
    out = {'value': value, 'label': e.label.value, 'source': _source_text(e) or f'hlag_engine profile {key}'}
    if formula or e.formula:
        out['formula'] = formula or e.formula
    return out


def dashboard_profile(profile: Profile) -> dict:
    p = profile.parameters
    close = p['fx.usd_per_eur_close_2025_12_31'].value
    debt_eur = p['rates.financial_debt_total_eur'].value
    lease = _entry(profile, 'liquidity.annual_lease_repayments_usd', 12e6, 'annual_lease_repayments_usd / 12 / 1e6')
    values = {
        'liquidity.cash_usd_m': _entry(profile, 'liquidity.cash_and_equivalents_usd', 1e6),
        'liquidity.credit_lines_usd_m': _entry(profile, 'liquidity.unused_credit_lines_usd', 1e6),
        'liquidity.investments_usd_m': _entry(profile, 'liquidity.special_fund_usd', 1e6),
        'liquidity.reserve_usd_m': _entry(profile, 'liquidity.minimum_operational_reserve_usd', 1e6),
        'liquidity.count_liquid_investments': _entry(profile, 'liquidity.count_liquid_investments'),
        'liquidity.interest_in_ocf': _entry(profile, 'liquidity.interest_in_ocf'),
        'liquidity.ocf_daily_usd_m': _entry(profile, 'liquidity.baseline_ocf_daily_usd', 1e6),
        'liquidity.lease_monthly_usd_m': lease,
        'liquidity.capex_monthly_usd_m': _entry(profile, 'liquidity.capex_monthly_usd', 1e6),
        'rates.financial_debt_usd_m': {'value': debt_eur * close / 1e6, 'label': 'DERIVED',
                                       'formula': 'rates.financial_debt_total_eur x fx.usd_per_eur_close_2025_12_31 / 1e6',
                                       'source': 'hlag_engine profile'},
    }
    anchor = profile.anchor_snapshot
    trades = {t: {'series': INDEX_SERIES.get(t),  # Trade is a StrEnum, so plain trade names look it up
                  'volume_h1_teu': p[f'freight.h1_volume_teu.{t}'].value,
                  'rate_usd_per_teu': p[f'freight.p0.{t}'].value,
                  'invoice_currency': p[f'freight.invoice_currency.{t}'].value,
                  'non_usd_share': p[f'freight.non_usd_share.{t}'].value,
                  'volume_rate_label': p[f'freight.h1_volume_teu.{t}'].label.value}
              for t in TRADES}
    return {
        'profile_version': profile.profile_version, 'profile_hash': profile.content_hash,
        'source': 'hlag_engine active profile (read-only)',
        'balance_date': str(p['liquidity.cash_and_equivalents_usd'].as_of),
        'anchor_start': anchor.start.isoformat(), 'anchor_end': anchor.end.isoformat(), 'anchor_period': anchor.period,
        'trades': trades, 'values': values,
        'limitations': ['Synthetic exposure register generated by hlag_engine; never the live company position.',
                        'Profile is read-only in the gateway; versions are activated in the engine repository.'],
        'engine_parameters': {k: {'value': e.model_dump(mode='json')['value'], 'label': e.label.value, 'unit': e.unit,
                                  'kind': e.kind.value, 'source': _source_text(e), 'formula': e.formula,
                                  'allowed_range': list(e.allowed_range) if e.allowed_range else None,
                                  'allowed_values': e.model_dump(mode='json')['allowed_values'],
                                  'adjustable': e.adjustable, 'overridable': e.overridable, 'rationale': e.rationale,
                                  'status': e.status.value}
                              for k, e in p.items()},
    }


# ---------------------------------------------------------------- output conversion


def _m(x) -> float:
    return float(Decimal(x) / M) if x is not None else 0.0


PRESET_GROUP_ORDER = ('joint', 'module', 'sensitivity')


def preset_group(pid: PresetId) -> str:
    """The engine's own preset scope, collapsed for display: joint, module (one driver's library) or sensitivity."""
    scope = pid.scope.value
    if scope == 'joint':
        return 'joint'
    return 'sensitivity' if scope.startswith('sensitivity') else 'module'


FX_CODES = ('EUR', 'CAD', 'GBP', 'CNY', 'INR', 'BRL')


def dashboard_sub_id(owner: str, currency: str, sub_id: str) -> str:
    """fx-owned USD lines (policy hedge settlements) end in a date in hlag_engine, e.g.
    'fx:hedge:cad_operating_costs:fx:opex:CAD:2026-11-06'. The dashboard assigns them to a currency card by the
    sub_id suffix, so the hedged currency is appended: '...:2026-11-06:CAD'."""
    if owner != 'fx' or currency != 'USD' or sub_id.endswith(tuple(':' + c for c in FX_CODES)):
        return sub_id
    hedged = [part for part in sub_id.split(':') if part in FX_CODES]
    return f'{sub_id}:{hedged[-1]}' if hedged else sub_id


def map_missing(key: str, status: str, affects: str) -> str:
    """hlag_engine MissingInput as the prefixed strings dashboard_adapter.affected() matches."""
    if status == 'module_error':
        module = (affects or '').split(' ', 1)[0] or 'engine'
        return f'{module}.module_error:{key}'
    if key.split('.', 1)[0] in DRIVERS:
        return key
    if key.startswith('debt.'):
        return 'profile:rates:' + key
    return 'profile:' + key


def liquidity_inputs(inputs) -> dict:
    """The run's resolved liquidity inputs: T1 cash rolled forward from the last disclosure to T0 (P77), the tiers
    and the minimum reserve (after any per-run override). Headroom at T0 = cash_t0 + credit lines [+ special fund]
    - reserve."""
    return {'cash_t0_usd_m': _m(inputs.t1_start), 'credit_lines_usd_m': _m(inputs.t2_start),
            'special_fund_usd_m': _m(inputs.t3_start), 'count_special_fund': bool(inputs.count_t3),
            'reserve_usd_m': _m(inputs.reserve), 'roll_forward': dict(inputs.roll_forward or {})}


class EngineAdapter:
    """Same interface the gateway uses from RiskEngine: run, run_pair, presets, profile."""

    def __init__(self, market_db=None, state_db=None, *, profile=None, demo=False):
        self.shared = _Shared.get(market_db)
        self.demo_requested = bool(demo)
        if self.demo_requested:
            log.warning('demo mode requested with CASH_ENGINE=spec: ignored, running the real engine (flags.demo = false)')

    @property
    def store(self) -> ReadOnlyStore:
        return self.shared.store

    @property
    def engine_profile(self) -> Profile:
        return self.shared.profile

    @property
    def profile(self) -> dict:
        return dashboard_profile(self.shared.profile)

    def default_as_of(self) -> date:
        return default_as_of(self.store)

    # ------------------------------------------------------------ RiskEngine interface
    def presets(self, as_of: date | None = None) -> list[dict]:
        as_of = as_of or self.default_as_of()
        base = ScenarioParams(name='Baseline', start_date=as_of, source=ScenarioSource.CUSTOM)
        ctx = ProfileContext(RunContext.for_scenario(self.shared.profile, as_of, base, (), self.shared.registers))
        out = [{'id': 'baseline', 'name': 'Baseline', 'group': 'baseline', 'description': 'No shocks',
                'rationale': 'Reference run', 'scenario': {'name': 'Baseline', 'source': 'preset', 'preset_id': 'baseline'}}]
        built = []
        for pid, preset in LIBRARY.items():
            params, missing = build_preset(pid, ctx)
            if params is None:
                log.info('preset %s unavailable: %s', pid.value, [m.key for m in missing])
                continue
            built.append({'id': pid.value, 'name': preset.name, 'group': preset_group(pid),
                          'scope': pid.scope.value, 'description': preset.description,
                          'rationale': preset.rationale, 'scenario': to_dashboard_scenario(params)})
        # joint, then module, then the sensitivity family; library order within each group (sort is stable)
        return out + sorted(built, key=lambda p: PRESET_GROUP_ORDER.index(p['group']))

    def run(self, as_of: date, scenario=None, adjustments=None) -> dict:
        dash = normalise(scenario, as_of)
        params = to_engine_params(dash, as_of)
        result, _ = self._run(as_of, dash, params, adjustments)
        return result

    def run_pair(self, as_of: date, scenario, state_change, adjustments=None) -> dict:
        dash = normalise(scenario, as_of)
        params = to_engine_params(dash, as_of)
        change, dash_b = {}, copy.deepcopy(dash)
        for key, value in (state_change or {}).items():
            if key not in {'overrides', 'hedge_tests', 'rates'}:
                raise ValueError('Pair only accepts state changes')
            if key == 'rates':
                if set(value) - {'zim_tranche_on'}:
                    raise ValueError('Only ZIM is a rates state field')
                change['zim_tranche_on'] = value['zim_tranche_on']
                dash_b['rates'] = {**dash_b.get('rates', {}), **value}
            elif key == 'hedge_tests':
                hedges = normalise({'hedge_tests': value}, as_of)['hedge_tests']
                change['hedge_tests'] = to_engine_params({'start_date': as_of.isoformat(), 'hedge_tests': hedges},
                                                         as_of).model_dump(mode='json')['hedge_tests']
                dash_b['hedge_tests'] = hedges
            else:
                change['overrides'] = value
                dash_b['overrides'] = {**dash_b.get('overrides', {}), **value}
        changed, explicit_zim = apply_state_change(params, change)
        run_a, res_a = self._run(as_of, dash, params, adjustments)
        run_b, res_b = self._run(as_of, dash_b, changed, adjustments, fill_zim=not explicit_zim)
        diff = difference(res_a, res_b).model_dump(mode='json')
        return {'label': 'state comparison', 'run_a': run_a, 'run_b': run_b,
                'difference': {'min_headroom_usd_m': run_b['headroom']['scenario_min_usd_m'] - run_a['headroom']['scenario_min_usd_m'],
                               'attribution_usd_m': {k: run_b['attribution'][k] - run_a['attribution'][k] for k in run_b['attribution']},
                               'line_totals_usd_m': diff['line_totals_usd_m']}}

    # ------------------------------------------------------------ internals
    def _run(self, as_of: date, dash: dict, params: ScenarioParams, adjustments, fill_zim: bool = True):
        adjs = to_engine_adjustments(adjustments, as_of)
        with self.shared.lock:
            self.shared.engine.gateway_adjustments = adjs
            try:
                res = self.shared.engine.run_scenario(params, as_of, write=False, fill_zim=fill_zim)
            finally:
                self.shared.engine.gateway_adjustments = []
            resolver = RunContext.for_scenario(self.shared.profile, as_of, res.record.params, tuple(adjs),
                                               self.shared.registers)
            rows = {sid: self.store.latest_on_or_before(sid, as_of) for sid in res.record.market_snapshot}
        return self._result(res, as_of, dash, adjustments or [], resolver, rows), res

    def _result(self, res: RunResult, as_of: date, dash: dict, adjustments: list, resolver, rows) -> dict:
        rec, summ, liq = res.record, res.summary, res.liquidity
        profile = self.shared.profile
        flags_t = TreatmentFlags.from_resolver(resolver)

        lines = []
        for pair in res.pairs:
            ref = pair.ref
            base_usd = pair.base.amount_usd_base if pair.base else Decimal(0)
            scen_usd = pair.scen.amount_usd_scenario if pair.scen else Decimal(0)
            lines.append({'owner': ref.owner_spec.value, 'line_code': ref.line_code.value,
                          'sub_id': dashboard_sub_id(ref.owner_spec.value, ref.currency.value, ref.sub_id),
                          'currency': ref.currency.value, 'date': ref.date.isoformat(),
                          'amount_native_m': _m(pair.b if pair.scen else pair.a),
                          'fx_rate': float(pair.y if pair.scen else pair.x),
                          'treatment': line_treatment(ref, flags_t).value,
                          'amount_native_base_m': _m(pair.a), 'amount_native_scenario_m': _m(pair.b),
                          'fx_rate_base': float(pair.x), 'fx_rate_scenario': float(pair.y),
                          'amount_usd_base_m': _m(base_usd), 'amount_usd_scenario_m': _m(scen_usd),
                          'delta_usd_m': _m(pair.delta_usd), 'label': ref.label.value, 'synthetic': True})

        paths = liq.paths
        baseline_path = [{'date': d.isoformat(), 'cash_usd_m': _m(liq.base.t1[d]), 'headroom_usd_m': _m(liq.base.headroom[d])}
                         for d in paths.dates]
        scenario_path = [{'date': d.isoformat(), 'cash_usd_m': _m(liq.scen.t1[d]), 'headroom_usd_m': _m(liq.scen.headroom[d])}
                         for d in paths.dates]
        path_components = [{'date': d.isoformat(), 'baseline_flow_usd_m': _m(paths.baseline_flow.get(d, 0)),
                            'anchor_usd_m': _m(paths.anchor.get(d, 0)), 'delta_usd_m': _m(paths.delta.get(d, 0))}
                           for d in paths.dates]

        h = summ.headroom
        headroom = {'baseline_min_usd_m': _m(liq.h_b), 'baseline_min_date': liq.h_b_date.isoformat(),
                    'scenario_min_usd_m': _m(liq.h_s), 'scenario_min_date': liq.h_s_date.isoformat(),
                    'consumed_pct': None if liq.c_pct is None else float(liq.c_pct),
                    'first_breach_date': liq.breach.isoformat() if liq.breach else None,
                    'shortfall_tier': h.shortfall_tier.value}
        attribution = {**{k: _m(v) for k, v in res.attribution.bars().items()}, 'total': _m(res.attribution.total)}

        material = rec.results.get('material_series', {})
        missing = ([f'market:{s}' for s in material.get('missing', [])] + [f'stale:{s}' for s in material.get('stale', [])]
                   + [map_missing(m.key, m.status, m.affects) for m in res.missing])

        snapshot = {}
        for sid, e in rec.market_snapshot.items():
            row = rows.get(sid)
            snapshot[sid] = {'series_id': sid, 'date': e.value_date.isoformat(), 'value': float(e.value), 'unit': e.unit,
                             'label': e.label.value, 'source': row.source if row else None,
                             'source_ref': row.source_ref if row else None, 'confirmed': int(e.confirmed), 'filled': int(e.filled),
                             'days_since_print': e.days_since_print, 'stale': e.stale,
                             'retrieved_at': row.retrieved_at.isoformat() if row else None}

        volume = {'baseline': defaultdict(float), 'scenario': defaultdict(float)}
        for pt, name in ((PassType.BASE, 'baseline'), (PassType.SCENARIO, 'scenario')):
            for v in res.outputs[pt]['freight'].volume_lines:
                volume[name][f'{v.trade.value}:{v.month.isoformat()[:7]}'] += float(v.teu)

        by_date = defaultdict(Decimal)
        for amounts in liq.anchor.by_driver.values():
            for d, a in amounts.items():
                by_date[d.isoformat()] += a
        anchor_adjustment = {'by_driver_usd_m': {g: _m(sum(v.values(), Decimal(0))) for g, v in liq.anchor.by_driver.items()},
                             'by_date': {d: _m(v) for d, v in sorted(by_date.items())}, 'label': 'ASSUMPTION',
                             'computed': liq.anchor.computed, 'note': liq.anchor.note}

        margin = liq.margin.get('scenario')
        memo = res.outputs[PassType.BASE]['fx'].diagnostics.get('memo_positions_usd', {})
        notes = rec.results.get('notes', {})
        limitations = [f'{module}: {n}' for module, items in notes.items() for n in items]
        limitations.append(f'Calculated by hlag_engine (profile {profile.profile_version}) read-only; the run is not '
                           'written to the engine store.')
        if adjustments:
            limitations.append('Gateway-approved adjustments carry no confidence; it is set to 0 (not used in calculations).')
        if self.demo_requested:
            limitations.append('Demo mode was requested but is not available with CASH_ENGINE=spec; real engine inputs were used.')

        params_json = rec.params.model_dump(mode='json')
        adj_json = [a.model_dump(mode='json') for a in rec.adjustments]
        snap_json = {sid: e.model_dump(mode='json') for sid, e in rec.market_snapshot.items()}
        run_id = 'spec_' + hashlib.sha256(canonical({
            'as_of': as_of.isoformat(), 'profile': profile.content_hash, 'params': params_json,
            'adjustments': adj_json, 'snapshot': snap_json}).encode()).hexdigest()[:20]
        derived_hash = hashlib.sha256(canonical({'derived': rec.derived_inputs, 'snapshot': snap_json}).encode()).hexdigest()
        p = profile.parameters
        return {
            'as_of': as_of.isoformat(), 'profile': dashboard_profile(profile), 'scenario': dash,
            'engine_params': params_json, 'adjustments': adjustments, 'market_snapshot': snapshot,
            'anchor_snapshot': {s: e.value for s, e in profile.anchor_snapshot.series.items()},
            'demo': False, 'historical_inputs_hash': derived_hash,
            'locked_fx_snapshot': {c: p[f'fx.lock_rate_usd_per_unit.{c}'].value for c in ('INR', 'BRL')},
            'run_id': run_id, 'profile_version': profile.profile_version, 'profile_hash': profile.content_hash,
            'status': summ.status.value,
            'status_reasons': list(rec.results.get('status_reasons', [])),
            'flags': {**rec.flags.model_dump(mode='json'), 'demo': False},
            'missing_inputs': sorted(set(missing)),
            'baseline_path': baseline_path, 'scenario_path': scenario_path, 'path_components': path_components,
            'attribution': attribution, 'attribution_usd_m': dict(attribution), 'headroom': headroom,
            'summary': {**headroom, 'net_delta_usd_m': attribution['total']},
            'opportunity': summ.opportunity, 'lines': lines,
            'volume_lines': {k: dict(v) for k, v in volume.items()},
            'anchor_adjustment': anchor_adjustment,
            'liquidity_inputs': liquidity_inputs(liq.inputs),
            'desk_exceptions': [{**e.model_dump(mode='json'), 'side': e.driver.value if e.driver else e.kind.value}
                                for e in summ.desk_exceptions],
            'units': UNITS,
            'margin_test': {'applicable': margin is not None, 'passed': bool(margin['passed']) if margin else True,
                            'minimum_usd_m': _m(margin['lowest']) if margin else None},
            'limitations': limitations,
            # Translation memo (spec 9A.5, D19): the engine's position x (scenario rate at horizon end / base rate - 1).
            # Balance-sheet revaluation, not cash; never on the liquidity path.
            'translation_memo': {'positions_usd_m': {str(c): float(v) / 1e6 for c, v in memo.items()},
                                 'revaluation_usd_m': {str(c): float(v) / 1e6
                                                       for c, v in rec.results.get('translation_memo_usd', {}).items()},
                                 'horizon_end': paths.dates[-1].isoformat() if paths.dates else None,
                                 'on_liquidity_path': False, 'label': 'DERIVED'},
            'memo_positions': {'CNY_long_usd_m': float(memo.get('CNY', 0)) / 1e6,
                               'GBP_short_usd_m': -float(memo.get('GBP', 0)) / 1e6, 'on_liquidity_path': False},
            'engine': {'name': 'hlag_engine', 'store': self.store.path, 'read_only': True},
        }


# ---------------------------------------------------------------- data for the /api/v1/engine endpoints


def market_overview(shared: _Shared, as_of: date | None = None) -> dict:
    from hlag_engine.data.series import Frequency
    from hlag_engine.engine.snapshot import build_snapshot
    from hlag_engine.engine.tier_a import SeriesSource, volatility_regime
    with shared.lock:
        as_of = as_of or default_as_of(shared.store)
        snapshot = build_snapshot(shared.store, as_of)
        source = SeriesSource(shared.store, as_of)
        series = []
        for sid, spec in SERIES.items():
            row = shared.store.latest_on_or_before(sid, as_of)
            prints = source.prints(sid)
            snap = snapshot.get(sid)
            item = {'series_id': sid, 'unit': spec.unit, 'frequency': spec.frequency.value,
                    'value': None, 'date': None, 'weekly_change': None, 'weekly_change_unit': None,
                    'weekly_change_pct': None, 'previous': None,
                    'volatility_regime': None, 'volatility_ratio': None, 'stale': True, 'status': 'MISSING',
                    'provenance': None, 'confirmed': None, 'source': None, 'source_ref': None,
                    'licence': LICENCE[spec.source_kind]}
            if snap is not None:
                item.update(value=snap.value, date=snap.value_date.isoformat(), stale=snap.stale,
                            status='STALE' if snap.stale else 'OK', provenance=snap.label.value, confirmed=snap.confirmed,
                            days_since_print=snap.days_since_print,
                            source=row.source if row else None, source_ref=row.source_ref if row else None)
            if prints:
                last = prints[-1]
                if spec.frequency is Frequency.WEEKLY:
                    prev = prints[-2] if len(prints) > 1 else None
                else:  # the print on or before 7 days earlier (Tier A "1w" window)
                    prev = next((q for q in reversed(prints[:-1]) if q.date <= last.date - timedelta(days=7)), None)
                if prev is not None:
                    change = last.value - prev.value
                    pct_series = spec.unit == '%'
                    item.update(previous={'date': prev.date.isoformat(), 'value': prev.value},
                                weekly_change=change * 100 if pct_series else change,
                                weekly_change_unit='bp' if pct_series else spec.unit,
                                weekly_change_pct=None if pct_series or not prev.value else change / prev.value * 100)
                regime = volatility_regime(prints, spec.frequency)
                if regime:
                    item.update(volatility_regime=regime[0], volatility_ratio=round(regime[1], 4))
                else:
                    item['volatility_note'] = 'not enough history for the volatility regime'
            series.append(item)
    return {'as_of': as_of.isoformat(), 'store': shared.store.path, 'series': series,
            'regime_rule': 'short/long std of log changes (daily 20 vs 260, weekly 8 vs 52); calm < 1.25 <= elevated <= 2.0 < stressed',
            'available': sum(1 for s in series if s['value'] is not None), 'total': len(series)}


def assumptions(shared: _Shared) -> dict:
    profile = shared.profile
    items = []
    for key, e in profile.parameters.items():
        if not e.overridable:
            continue
        dumped = e.model_dump(mode='json')
        items.append({'key': key, 'value': dumped['value'], 'unit': e.unit, 'label': e.label.value,
                      'kind': e.kind.value, 'allowed_range': list(e.allowed_range) if e.allowed_range else None,
                      'allowed_values': dumped['allowed_values'], 'rationale': e.rationale,
                      'adjustable': e.adjustable, 'status': e.status.value})
    ranges = load_scenario_ranges().model_dump(mode='json')
    return {'profile_version': profile.profile_version, 'count': len(items), 'parameters': items,
            'scenario_ranges': ranges,
            'overrides': 'POST /api/v1/risk/run and POST /api/v1/dashboard accept "overrides": {key: value} using these '
                         'keys and units (whole percents for *_pct, fractions for *_share, bp for *_bp). adjustable = '
                         'also changeable by approved news adjustments.'}


# ---------------------------------------------------------------- explicit market data refresh (the only store write)


def refresh_market(shared: _Shared, actor: str, as_of: date, sources=None, years: int = 2) -> dict:
    """Run hlag_engine's API data load (ECB, NY Fed, FRED) into the engine store, started by a named person.

    This is the only write the gateway makes to the engine store. A writable connection is opened for this call
    only; the shared lock keeps runs from reading while the load's transaction is open. The engine writes its own
    market_data_load audit entry, with this person as the actor. WCI and fuel are not fetched (weekly CSV only).
    """
    import httpx
    from hlag_engine.data.connectors import default_sources
    from hlag_engine.data.ingest import load_api_history
    from hlag_engine.data.series import AGENT_SERIES

    actor = (actor or '').strip()
    if not actor:
        raise ValueError('A name is required to refresh market data')
    audit_actor = f'{actor} (dashboard: Refresh market data)'
    client = None
    if sources is None:
        client = httpx.Client(follow_redirects=True)
        sources = default_sources(client)
    try:
        with shared.lock:
            store = Store(shared.store.path)
            try:
                report = load_api_history(store, sources, as_of, years=years, actor=audit_actor)
                entry = store.list_audit(action='market_data_load', object_id=f'api_history:{as_of.isoformat()}')[-1]
            finally:
                store.close()
    finally:
        if client is not None:
            client.close()
    series = [{'series_id': sid, 'source': r.source, 'path': r.path, 'prints': r.prints, 'filled': r.filled,
               'missing': len(r.missing_dates), 'last_print': r.last_print.isoformat() if r.last_print else None,
               'days_since_print': r.days_since_print, 'stale': r.stale, 'errors': list(r.errors),
               'rejected': [{'date': x.date.isoformat(), 'value': x.value, 'reasons': list(x.reasons)} for x in r.rejected]}
              for sid, r in report.series.items()]
    return {'as_of': report.as_of.isoformat(), 'start': report.start.isoformat(), 'actor': audit_actor,
            'audit_entry_id': entry.entry_id, 'series': series,
            'loaded': sum(1 for s in series if s['prints'] > 0),
            'rejected_values': sum(len(s['rejected']) for s in series),
            'stale_series': report.stale_series, 'failed_series': [s['series_id'] for s in series if s['prints'] == 0],
            'not_fetched': list(AGENT_SERIES),
            'not_fetched_note': 'WCI and fuel prices are not fetched live; they come from the weekly CSV loaded in the '
                                'engine repository.'}


# ---------------------------------------------------------------- hypothetical fuel hedge test (spec 5.3), read-only

HEDGE_NOTE = ('Hypothetical hedge test · nothing is executed. Saving = change in the scenario minimum headroom with the '
              'hedge; cost = reduction under the mirrored scenario (every shock sign reversed). Strike = target price at T0.')


def hedge_options(shared: _Shared) -> dict:
    from hlag_engine.modules.bunker.hedge import INSTRUMENTS, TARGETS
    ranges = load_scenario_ranges()
    return {'module': 'bunker', 'as_of': default_as_of(shared.store).isoformat(),
            'targets': [{'id': k, 'label': v} for k, v in TARGETS.items()],
            'instruments': [{'id': i.value, 'label': 'Swap' if i.value == 'swap' else 'Fixed-price supply contract',
                             'margin_calls': i.value == 'swap'} for i in INSTRUMENTS],
            'coverage_pct_range': list(ranges.hedge_coverage_pct), 'tenor_months_range': list(ranges.hedge_tenor_months),
            'strike_rule': 'spot_at_T0', 'note': HEDGE_NOTE}


def _dated_by_month(items) -> list[dict]:
    months = defaultdict(float)
    for x in items:
        months[x.date.isoformat()[:7]] += x.usd_m
    return [{'month': m, 'usd_m': v} for m, v in sorted(months.items())]


def _desk(summary) -> list[dict]:
    return [{**e.model_dump(mode='json'), 'side': e.driver.value if e.driver else e.kind.value} for e in summary.desk_exceptions]


class HedgeAdapterMixin:
    """EngineAdapter.hedge_test: the same steps as hlag_engine.engine.compare.test_hedge (run_pair with and without
    the hedge, under the scenario and its mirror), but with run_scenario(write=False) and no audit entries, because
    the gateway opens the engine store read-only."""

    def hedge_test(self, as_of: date, scenario, hedge: dict, adjustments=None) -> dict:
        from hlag_engine.contracts.enums import LineCode
        from hlag_engine.contracts.hedge_test import HedgeTest
        from hlag_engine.contracts.pair_result import HedgeMetrics, MarginTest
        from hlag_engine.engine.compare import _dated, mirror, usd_m

        dash = normalise(scenario, as_of)
        params = to_engine_params(dash, as_of)
        test = HedgeTest(module='bunker', instrument=hedge['instrument'], target=hedge['target'],
                         coverage_pct=hedge['coverage_pct'], tenor_months=hedge['tenor_months'],
                         strike_rule='spot_at_T0', start_date=hedge.get('start_date') or as_of)
        change = {'hedge_tests': [*params.hedge_tests, test]}
        adjs = to_engine_adjustments(adjustments, as_of)
        engine = self.shared.engine

        def pair(p):
            changed, explicit_zim = apply_state_change(p, change)
            a = engine.run_scenario(p, as_of, write=False)
            b = engine.run_scenario(changed, as_of, write=False, fill_zim=not explicit_zim)
            return a, b

        with self.shared.lock:
            engine.gateway_adjustments = adjs
            try:
                given_a, given_b = pair(params)
                mirrored, notes = mirror(params)
                flip_a, flip_b = pair(mirrored)
            finally:
                engine.gateway_adjustments = []
        saving = given_b.liquidity.h_s - given_a.liquidity.h_s
        cost = flip_a.liquidity.h_s - flip_b.liquidity.h_s
        margin = given_b.liquidity.margin.get('scenario')
        if margin:
            mtest = MarginTest(passed=margin['shortfall_date'] is None, lowest_margin_eligible_balance_usd_m=usd_m(margin['lowest']))
        else:  # no swap: no margin calls; the lowest margin-eligible balance is the lowest T4
            mtest = MarginTest(passed=True, lowest_margin_eligible_balance_usd_m=usd_m(min(given_b.liquidity.scen.t4.values())))
        metrics = HedgeMetrics(saving_adverse_usd_m=usd_m(saving), cost_favourable_usd_m=usd_m(cost),
                               settlement_cash=_dated(given_b, test, LineCode.HEDGE_SETTLEMENT),
                               margin_cash=_dated(given_b, test, LineCode.MARGIN_CALL), margin_test=mtest)
        desk_without, desk_with = _desk(given_a.summary), _desk(given_b.summary)
        # test_hedge names the selected scenario's change the "saving" and the mirror's reduction the "cost", which
        # assumes the selected scenario raises fuel prices. Assign them by fuel direction so saving is always the
        # adverse (fuel up) side and cost the favourable (fuel down) side.
        move = params.bunker.vlsfo_shock_pct + params.bunker.hsfo_shock_pct
        given_change, mirror_change = usd_m(saving), -usd_m(cost)
        adverse_is = 'mirror' if move < 0 else 'scenario'
        on_t0 = sum(x.usd_m for x in metrics.margin_cash if x.date == as_of)
        return {
            'label': 'Hypothetical hedge test · nothing is executed', 'as_of': as_of.isoformat(),
            'hedge': test.model_dump(mode='json'), 'scenario': params.name,
            'metrics': metrics.model_dump(mode='json'),
            'settlement_by_month': _dated_by_month(metrics.settlement_cash),
            'margin_by_month': _dated_by_month(metrics.margin_cash),
            'margin_test': {**metrics.margin_test.model_dump(mode='json'),
                            'applicable': margin is not None,
                            'lowest_date': margin['lowest_date'].isoformat() if margin and margin['lowest_date'] else None,
                            'shortfall_date': margin['shortfall_date'].isoformat() if margin and margin['shortfall_date'] else None},
            'min_headroom_usd_m': {'without': usd_m(given_a.liquidity.h_s), 'with': usd_m(given_b.liquidity.h_s),
                                   'change': usd_m(saving), 'date_with': given_b.liquidity.h_s_date.isoformat()},
            'mirror_min_headroom_usd_m': {'without': usd_m(flip_a.liquidity.h_s), 'with': usd_m(flip_b.liquidity.h_s)},
            'status': {'without': given_a.summary.status.value, 'with': given_b.summary.status.value},
            'desk_exceptions_with_hedge': desk_with,
            'desk_exceptions_raised_by_hedge': [e for e in desk_with if e not in desk_without],
            'mirror_notes': notes, 'note': HEDGE_NOTE, 'read_only': True,
            'engine_horizon_end': given_b.liquidity.paths.dates[-1].isoformat(),
            'fuel_move': 'up' if move > 0 else 'down' if move < 0 else 'none',
            'change_in_min_headroom_usd_m': {'scenario': given_change, 'mirror': mirror_change},
            'adverse_is': adverse_is,
            'saving_adverse_usd_m': given_change if adverse_is == 'scenario' else mirror_change,
            'cost_favourable_usd_m': -(mirror_change if adverse_is == 'scenario' else given_change),
            # Margin dated on T0 itself (a step price move at T0) falls before the liquidity path, which starts the
            # day after T0, so the engine leaves it out of the path and the margin-eligible test.
            'margin_on_t0_usd_m': on_t0,
            'margin_on_t0_note': ('Margin dated on T0 is not on the liquidity path or in the margin-eligible test '
                                  '(the path starts the day after T0).') if on_t0 else None,
        }


EngineAdapter.hedge_test = HedgeAdapterMixin.hedge_test
