# Hand-collected weekly history (spec 3.6)

The WCI routes and the four fuel series have no free API. About one year of weekly history is
collected by hand, once, into a CSV in exactly this format (start from
`weekly_history_template.csv`):

| Column | Rule |
| :--- | :--- |
| `series_id` | One of `WCI_SHA_RTM`, `WCI_SHA_LAX`, `WCI_RTM_NYC`, `WCI_COMPOSITE` (unit `USD/FEU`) or `VLSFO_SIN`, `VLSFO_RTM`, `HSFO_SIN`, `HSFO_RTM` (unit `USD/t`) |
| `date` | Print date, `YYYY-MM-DD` |
| `value` | Positive number, no thousands separators (`2150.5`, not `2,150.5`) |
| `unit` | Exactly the series unit above (`USD/TEU` or `USD/bbl` is rejected) |
| `source_url` | The `http(s)` page the value was read from, or a Bloomberg Terminal reference `Bloomberg Terminal, <ticker> Index (<field>)` (required, P89) |
| `notes` | Optional free text |

Example row: `WCI_SHA_RTM,2026-01-08,2150.5,USD/FEU,https://example.com/wci-2026-01-08,`

The history must cover the anchor period (1 Jan – 30 Jun 2026) with no gap over 14 days, so
the anchor averages can be computed (spec 3.7).

Load it into a database:

    .venv/bin/python -m hlag_engine.data.cli load-csv --db hlag_engine/data/market.sqlite hlag_engine/data/history/weekly_history.csv

Any bad row rejects the whole file and lists every problem with its line number; nothing is
written. A week-on-week move beyond the plausibility band (fuel ±40%, WCI ±80%) is loaded but
reported as a warning and written to the audit log. Values are stored with label MARKET and
confirmed = true. Loading the same file twice changes nothing; a different value for an
already stored date needs `--replace`.

Licence line for these series (pending decision P91, replacing spec 3.6's line for the
Bloomberg-sourced history): "WCI: Drewry World Container Index via Bloomberg Terminal; fuel:
ClearLynx bunker assessments via Bloomberg Terminal. Licensed data (university licence), internal
non-commercial coursework use only; not for redistribution."

The real file, `weekly_history.csv`, holds raw Bloomberg values and is git-ignored: never commit
it, and never ship it in the package (P90).
