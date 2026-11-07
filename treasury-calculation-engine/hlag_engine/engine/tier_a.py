"""Tier A market alerts (spec 4.5): one engine evaluates every module's AlertRules on observed market data, P83.

Inputs are real prints (carry-forward rows excluded), dated on or before the evaluation date.
- pct_move: % change over the window; bp_move: change x 100 on series quoted in percent.
- spread_change: change over the window of series A - series B (same unit, e.g. USD/t).
- divergence: % change of A minus % change of B over the window, in percentage points.
- vol_multiple (the "or N x vol" part of a level): |latest log change| / standard deviation of the log changes
  over the rule's lookback, the latest change excluded.
- vol_regime: standard deviation of log changes over a short window over that of a long window (daily 20 vs 260
  observations, weekly 8 vs 52); calm below 1.25, elevated 1.25-2.0, stressed above 2.0 (ASSUMPTION). Active
  while elevated (warning) or stressed (severe).
A level fires when either of its conditions is met; severe wins over warning. Windows: 1d = the previous print of a
daily series; 1w = the previous print of a weekly series, or the print on or before 7 days earlier for a daily
series; 4w / 20d = 28 / 20 days back. Synthetic series: VLSFO_BLENDED (hub-weighted VLSFO) and
FREIGHT_REALISED_<trade> (the realised-rate estimate of scheduled baseline runs); a rule without enough data is
reported as not evaluable, never as firing. An alert on an unconfirmed retrieved value carries the flag.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

from hlag_engine.company_profile.resolve import RunContext, resolve
from hlag_engine.company_profile.schema import Profile
from hlag_engine.contracts.alert_rule import AlertRule, AlertRuleType, ThresholdLevel
from hlag_engine.contracts.enums import AlertSeverity, ProvenanceLabel
from hlag_engine.data.series import SERIES, Frequency
from hlag_engine.data.store import Store

VOL_REGIME = {"elevated": 1.25, "stressed": 2.0}
REGIME_WINDOWS = {Frequency.DAILY: (20, 260), Frequency.WEEKLY: (8, 52)}
WINDOW_DAYS = {"1w": 7, "4w": 28, "20d": 20}
HISTORY_DAYS = 420


@dataclass(frozen=True)
class Print:
    date: date
    value: float
    confirmed: bool = True
    label: ProvenanceLabel = ProvenanceLabel.MARKET


@dataclass
class Outcome:
    rule: AlertRule
    evaluable: bool
    severity: AlertSeverity | None = None
    observed: float | None = None  # in the rule's unit
    vol_multiple: float | None = None
    inputs: list[tuple[str, float, date, ProvenanceLabel]] = field(default_factory=list)
    unconfirmed: bool = False
    reason: str = ""


@dataclass
class MonitorEntry:
    series_id: str
    level: float
    as_of: date
    change_pct: float | None
    regime: str | None
    regime_ratio: float | None


class SeriesSource:
    """Real prints per series, including the synthetic ones."""

    def __init__(self, store: Store, as_of: date, profile: Profile | None = None):
        self.store, self.as_of, self.profile = store, as_of, profile
        self._cache: dict[str, list[Print]] = {}

    def frequency(self, sid: str) -> Frequency:
        if sid in SERIES:
            return SERIES[sid].frequency
        return Frequency.WEEKLY  # VLSFO_BLENDED, FREIGHT_REALISED_*

    def prints(self, sid: str) -> list[Print]:
        if sid not in self._cache:
            self._cache[sid] = self._load(sid)
        return self._cache[sid]

    def _load(self, sid: str) -> list[Print]:
        start = self.as_of - timedelta(days=HISTORY_DAYS)
        if sid in SERIES:
            return [Print(r.date, r.value, r.confirmed, r.label)
                    for r in self.store.get_series(sid, start, self.as_of, include_filled=False)]
        if sid == "VLSFO_BLENDED":
            w = 0.6 if self.profile is None else float(resolve("bunker.hub_weight_sin", self.as_of,
                                                               RunContext(self.profile, self.as_of)))
            sin = {p.date: p for p in self.prints("VLSFO_SIN")}
            rtm = {p.date: p for p in self.prints("VLSFO_RTM")}
            return [Print(d, w * sin[d].value + (1 - w) * rtm[d].value, sin[d].confirmed and rtm[d].confirmed)
                    for d in sorted(set(sin) & set(rtm))]
        if sid.startswith("FREIGHT_REALISED_"):
            return realised_rate_series(self.store, sid.removeprefix("FREIGHT_REALISED_"), self.as_of)
        return []


def realised_rate_series(store: Store, trade: str, as_of: date) -> list[Print]:
    """The freight module's realised-rate estimate from scheduled baseline runs (a model estimate, 6.9)."""
    from hlag_engine.engine.scheduled import BASELINE_NAME

    rows = store.conn.execute(
        "SELECT record_json FROM run_records WHERE as_of <= ? AND as_of >= ? AND record_json LIKE ? ORDER BY as_of",
        (as_of.isoformat(), (as_of - timedelta(days=35)).isoformat(), f'%"name":"{BASELINE_NAME}"%'),
    ).fetchall()
    out = {}
    from hlag_engine.contracts.run_record import RunRecord

    for r in rows:
        rec = RunRecord.model_validate_json(r["record_json"])
        if rec.params.source.value != "scheduled" or rec.params.name != BASELINE_NAME or rec.as_of > as_of:
            continue
        metric = rec.results.get("module_metrics", {}).get("base", {}).get("freight", {}).get(
            f"realised_rate_usd_per_teu.{trade}")
        if metric and metric.get("value") is not None:
            out[rec.as_of] = Print(rec.as_of, float(metric["value"]), True, ProvenanceLabel.ASSUMPTION)
    return [out[d] for d in sorted(out)]


def _previous(prints: list[Print], window: str, freq: Frequency) -> Print | None:
    if len(prints) < 2:
        return None
    last = prints[-1]
    if window == "1d" or (window == "1w" and freq is Frequency.WEEKLY):
        return prints[-2]
    days = WINDOW_DAYS.get(window)
    if days is None and window.endswith(" obs"):
        n = int(window.split()[0])
        return prints[-1 - n] if len(prints) > n else None
    target = last.date - timedelta(days=days or 1)
    earlier = [p for p in prints[:-1] if p.date <= target]
    return earlier[-1] if earlier else None


def _log_changes(prints: list[Print]) -> list[float]:
    return [math.log(b.value / a.value) for a, b in zip(prints, prints[1:]) if a.value > 0 and b.value > 0]


def _std(xs: Sequence[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def volatility_regime(prints: list[Print], freq: Frequency) -> tuple[str, float] | None:
    short, long_ = REGIME_WINDOWS[freq]
    changes = _log_changes(prints)
    if len(changes) < max(short, 3) + 1:
        return None
    s, l = _std(changes[-short:]), _std(changes[-long_:])
    if not l:
        return None
    ratio = s / l
    label = "stressed" if ratio > VOL_REGIME["stressed"] else "elevated" if ratio >= VOL_REGIME["elevated"] else "calm"
    return label, ratio


def _level_met(level: ThresholdLevel, observed: float | None, multiple: float | None) -> bool:
    by_value = level.value is not None and observed is not None and abs(observed) >= level.value
    by_vol = level.vol_multiple is not None and multiple is not None and multiple >= level.vol_multiple
    return by_value or by_vol


def evaluate_rule(rule: AlertRule, source: SeriesSource) -> Outcome:
    series = [source.prints(s) for s in rule.series]
    if any(len(p) < 2 for p in series):
        return Outcome(rule, False, reason="not enough observations")
    freq = source.frequency(rule.series[0])
    a = series[0]
    unconfirmed = any(not p[-1].confirmed for p in series)
    inputs = [(sid, p[-1].value, p[-1].date, p[-1].label) for sid, p in zip(rule.series, series)]

    if rule.type is AlertRuleType.VOL_REGIME:
        regime = volatility_regime(a, freq)
        if regime is None:
            return Outcome(rule, False, reason="not enough observations for the volatility regime")
        label, ratio = regime
        severity = AlertSeverity.SEVERE if label == "stressed" else AlertSeverity.WARNING if label == "elevated" else None
        return Outcome(rule, True, severity, ratio, None, inputs, unconfirmed, f"regime {label}")

    prev_a = _previous(a, rule.window, freq)
    if prev_a is None:
        return Outcome(rule, False, reason=f"no observation for the {rule.window} window")
    observed: float
    if rule.type in (AlertRuleType.PCT_MOVE, AlertRuleType.VOL_MULTIPLE):
        observed = (a[-1].value / prev_a.value - 1) * 100
    elif rule.type is AlertRuleType.BP_MOVE:
        observed = (a[-1].value - prev_a.value) * 100
    else:
        b = series[1]
        prev_b = _previous(b, rule.window, source.frequency(rule.series[1]))
        if prev_b is None:
            return Outcome(rule, False, reason=f"no observation of {rule.series[1]} for the window")
        if rule.type is AlertRuleType.SPREAD_CHANGE:
            observed = (a[-1].value - b[-1].value) - (prev_a.value - prev_b.value)
        else:  # divergence
            observed = (a[-1].value / prev_a.value - 1) * 100 - (b[-1].value / prev_b.value - 1) * 100
        inputs.append((f"{rule.series[1]} (previous)", prev_b.value, prev_b.date, prev_b.label))
    inputs.append((f"{rule.series[0]} (previous)", prev_a.value, prev_a.date, prev_a.label))

    multiple = None
    if rule.lookback and any(t.vol_multiple for t in (rule.thresholds.warning, rule.thresholds.severe)):
        changes = _log_changes(a)
        history = changes[-1 - rule.lookback:-1]
        vol = _std(history)
        if vol and a[-1].value > 0 and prev_a.value > 0:
            multiple = abs(math.log(a[-1].value / prev_a.value)) / vol
    severity = None
    if _level_met(rule.thresholds.severe, observed, multiple):
        severity = AlertSeverity.SEVERE
    elif _level_met(rule.thresholds.warning, observed, multiple):
        severity = AlertSeverity.WARNING
    return Outcome(rule, True, severity, observed, multiple, inputs, unconfirmed)


def evaluate_rules(rules: Sequence[AlertRule], source: SeriesSource) -> list[Outcome]:
    return [evaluate_rule(r, source) for r in rules]


def market_monitor(source: SeriesSource, series_ids: Sequence[str]) -> list[MonitorEntry]:
    out = []
    for sid in series_ids:
        prints = source.prints(sid)
        if not prints:
            continue
        freq = source.frequency(sid)
        change = (prints[-1].value / prints[-2].value - 1) * 100 if len(prints) >= 2 and prints[-2].value else None
        regime = volatility_regime(prints, freq)
        out.append(MonitorEntry(sid, prints[-1].value, prints[-1].date, change,
                                regime[0] if regime else None, regime[1] if regime else None))
    return out
