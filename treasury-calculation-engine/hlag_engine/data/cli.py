"""Command line for administrators: load API history, load the weekly CSV, preview anchors.

    python -m hlag_engine.data.cli load-api --db hlag_engine/data/market.sqlite [--as-of YYYY-MM-DD] [--years 2]
    python -m hlag_engine.data.cli load-csv --db hlag_engine/data/market.sqlite FILE [--replace]
    python -m hlag_engine.data.cli anchor-preview --db hlag_engine/data/market.sqlite
    python -m hlag_engine.data.cli daily --db hlag_engine/data/market.sqlite [--as-of YYYY-MM-DD] [--no-load]

Writing a new profile version and activating it are deliberately left to the Python functions
hlag_engine.data.anchor.write_anchor_profile_version and hlag_engine.data.profile_admin.activate_profile_version.
"""

import argparse
import json
import sys
from datetime import date

import httpx

from hlag_engine.company_profile.schema import load_profile
from hlag_engine.data.anchor import compute_anchor_values
from hlag_engine.data.connectors import default_sources
from hlag_engine.data.csv_history import HistoryCSVError, load_history_csv
from hlag_engine.data.ingest import load_api_history
from hlag_engine.data.store import Store


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hlag_engine.data.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("load-api", help="load API history (ECB, NY Fed, FRED)")
    api.add_argument("--db", required=True)
    api.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    api.add_argument("--years", type=int, default=2)
    csv_cmd = sub.add_parser("load-csv", help="load the hand-collected weekly history CSV")
    csv_cmd.add_argument("--db", required=True)
    csv_cmd.add_argument("file")
    csv_cmd.add_argument("--replace", action="store_true")
    csv_cmd.add_argument("--actor", default="admin")
    prev = sub.add_parser("anchor-preview", help="show the anchor values loaded history would give")
    prev.add_argument("--db", required=True)
    daily = sub.add_parser("daily", help="daily job: load API data, Tier A rules, scheduled runs, baseline change")
    daily.add_argument("--db", required=True)
    daily.add_argument("--as-of", type=date.fromisoformat, default=None, help="T0 (default: latest loaded date)")
    daily.add_argument("--no-load", action="store_true", help="skip the API data load")
    daily.add_argument("--years", type=int, default=2)
    args = parser.parse_args(argv)

    store = Store(args.db)
    try:
        if args.command == "load-api":
            with httpx.Client(follow_redirects=True) as client:
                report = load_api_history(store, default_sources(client), args.as_of, years=args.years)
            summary = {
                sid: {"source": r.source, "path": r.path, "prints": r.prints, "filled": r.filled,
                      "last_print": r.last_print, "stale": r.stale, "rejected": len(r.rejected), "errors": r.errors}
                for sid, r in report.series.items()
            }
            _print({"as_of": report.as_of, "start": report.start, "series": summary})
            return 0
        if args.command == "load-csv":
            try:
                report = load_history_csv(store, args.file, actor=args.actor, replace=args.replace)
            except HistoryCSVError as exc:
                print("\n".join(exc.errors), file=sys.stderr)
                return 1
            _print(report.to_dict())
            return 0
        if args.command == "daily":
            from hlag_engine.engine.daily import run_daily
            from hlag_engine.engine.run import Engine

            loader = None
            if not args.no_load:
                client = httpx.Client(follow_redirects=True)

                def loader(st, load_date):
                    return load_api_history(st, default_sources(client), load_date, years=args.years)

            _print(run_daily(Engine(store), args.as_of, loader, load_date=args.as_of or date.today()))
            return 0
        _print(compute_anchor_values(store, load_profile()).to_dict())
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
