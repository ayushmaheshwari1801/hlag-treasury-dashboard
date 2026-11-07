"""Loading API history into the store (spec 3.6).

For each series: fetch from the first source that supplies it (falling back to the next one on a
source error, e.g. ECB -> Frankfurter for FX), run the shared checks, carry daily series forward
on non-publication days (filled = true, label MARKET), store, and write one audit entry.

Defaults P24, P27:
- days_since_print is calendar days since the last real print; daily series are filled for every
  calendar day from their first print up to the load date (as_of).
- An empty value is a non-publication day (counted as missing). A value that fails a check
  (non-positive where it applies, wrong unit) is not stored; it is listed in the load report and
  the audit log, and the gap is carried forward.
- API series are public reference data: stored with confirmed = true and label MARKET.
"""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.data.checks import MISSING_VALUE, days_since_print, is_stale, value_checks
from hlag_engine.data.connectors.base import MarketDataSource, Observation, SourceError
from hlag_engine.data.series import API_SERIES, Frequency, MarketDataSettings, load_market_settings, spec_for
from hlag_engine.data.store import MarketRow, Store

DEFAULT_HISTORY_YEARS = 2


@dataclass
class RejectedValue:
    date: date
    value: float | None
    reasons: list[str]


@dataclass
class SeriesLoad:
    series_id: str
    source: str | None = None
    path: str | None = None  # access path used, e.g. ecb_data_api, frankfurter, fred_api, fred_csv
    prints: int = 0
    filled: int = 0
    missing_dates: list[date] = field(default_factory=list)
    rejected: list[RejectedValue] = field(default_factory=list)
    last_print: date | None = None
    days_since_print: int | None = None
    stale: bool = True
    errors: list[str] = field(default_factory=list)  # source errors, including ones a fallback recovered


@dataclass
class LoadReport:
    as_of: date
    start: date
    series: dict[str, SeriesLoad]

    @property
    def stale_series(self) -> list[str]:
        return [s for s, r in self.series.items() if r.stale]

    @property
    def paths(self) -> dict[str, str | None]:
        return {s: r.path for s, r in self.series.items()}

    def to_dict(self) -> dict:
        return asdict(self)


def years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def build_rows(
    series_id: str, prints: list[Observation], as_of: date, source: str, retrieved_at, frequency: Frequency
) -> tuple[list[MarketRow], int]:
    """Real prints plus, for daily series, carry-forward rows up to as_of. Returns (rows, filled count)."""
    by_date = {o.date: o for o in prints}
    rows: list[MarketRow] = []
    filled = 0

    def row(day: date, obs: Observation, is_filled: bool) -> MarketRow:
        return MarketRow(
            series_id=series_id, date=day, value=float(obs.value), unit=obs.unit, source=source,
            label=ProvenanceLabel.MARKET, retrieved_at=retrieved_at, filled=is_filled,
            days_since_print=days_since_print(obs.date, day), confirmed=True, source_ref=obs.source_ref,
        )

    if frequency is Frequency.WEEKLY:
        return [row(o.date, o, False) for o in prints], 0
    day, last = prints[0].date, prints[0]
    while day <= as_of:
        if day in by_date:
            last = by_date[day]
            rows.append(row(day, last, False))
        else:
            rows.append(row(day, last, True))
            filled += 1
        day += timedelta(days=1)
    return rows, filled


def _fetch_with_fallback(
    sources: list[MarketDataSource], series_ids: list[str], start: date, end: date, report: dict[str, SeriesLoad]
) -> dict[str, tuple[list[Observation], str, str]]:
    """{series_id: (observations, source name, path)} using each series' first working source."""
    results: dict[str, tuple[list[Observation], str, str]] = {}
    pending = list(series_ids)
    tried: dict[str, set[int]] = {s: set() for s in series_ids}
    while pending:
        groups: dict[int, list[str]] = {}
        for sid in pending:
            idx = next(
                (i for i, src in enumerate(sources) if sid in src.series_ids and i not in tried[sid]), None
            )
            if idx is None:
                report[sid].errors.append("no source could supply this series")
                continue
            groups.setdefault(idx, []).append(sid)
        pending = []
        for idx, group in groups.items():
            source = sources[idx]
            for sid in group:
                tried[sid].add(idx)
            try:
                fetched = source.fetch(group, start, end)
            except SourceError as exc:
                for sid in group:
                    report[sid].errors.append(f"{source.name}: {exc}")
                pending += group
                continue
            for sid in group:
                obs = [o for o in fetched.observations if o.series_id == sid]
                results[sid] = (obs, fetched.source, fetched.path)
    return results


def load_api_history(
    store: Store,
    sources: list[MarketDataSource],
    as_of: date,
    series_ids: list[str] | tuple[str, ...] = API_SERIES,
    years: int = DEFAULT_HISTORY_YEARS,
    actor: str = "data_loader",
    settings: MarketDataSettings | None = None,
) -> LoadReport:
    """Load `years` of history up to as_of for the API series (3.6: two years by default)."""
    settings = settings or load_market_settings()
    for sid in series_ids:
        if sid not in API_SERIES:
            raise ValueError(f"{sid} is not an API series (WCI and fuel come from the CSV loader)")
    start = years_before(as_of, years)
    report = {sid: SeriesLoad(sid) for sid in series_ids}
    fetched = _fetch_with_fallback(sources, list(series_ids), start, as_of, report)
    retrieved_at = store.now()

    with store.transaction():
        for sid in series_ids:
            entry = report[sid]
            if sid not in fetched:
                continue
            observations, source_name, path = fetched[sid]
            entry.source, entry.path = source_name, path
            spec = spec_for(sid)
            good: dict[date, Observation] = {}
            for obs in sorted(observations, key=lambda o: o.date):
                if not start <= obs.date <= as_of:
                    continue
                failed = value_checks(spec, obs.value, obs.unit)
                if failed == [MISSING_VALUE]:
                    entry.missing_dates.append(obs.date)
                elif failed:
                    entry.rejected.append(RejectedValue(obs.date, obs.value, failed))
                else:
                    good[obs.date] = obs
            prints = [good[d] for d in sorted(good)]
            if not prints:
                entry.errors.append("no valid values in the period")
                continue
            rows, filled = build_rows(sid, prints, as_of, source_name, retrieved_at, spec.frequency)
            store.upsert_market_rows(rows)
            entry.prints, entry.filled = len(prints), filled
            entry.last_print = prints[-1].date
            entry.days_since_print = days_since_print(entry.last_print, as_of)
            entry.stale = is_stale(spec, settings, entry.days_since_print)

        result = LoadReport(as_of=as_of, start=start, series=report)
        store.append_audit(
            actor,
            "market_data_load",
            f"api_history:{as_of.isoformat()}",
            {
                "start": start,
                "as_of": as_of,
                "series": {
                    sid: {
                        "source": r.source, "path": r.path, "prints": r.prints, "filled": r.filled,
                        "missing": len(r.missing_dates),
                        "rejected": [{"date": x.date, "value": x.value, "reasons": x.reasons} for x in r.rejected],
                        "last_print": r.last_print, "days_since_print": r.days_since_print, "stale": r.stale,
                        "errors": r.errors,
                    }
                    for sid, r in report.items()
                },
            },
        )
    return result
