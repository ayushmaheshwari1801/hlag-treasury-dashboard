# Synthetic weekly history (tests only)

`weekly_history_sample.csv` has the shape of the real Bloomberg Terminal file
(`docs/handoff/price_history_filled.xlsx`, which is never committed, P90) but **every value is
synthetic**: Fridays 19 Dec 2025 – 3 Jul 2026, simple linear series, Bloomberg-style
`source_url` references (P89), the WCI week of 2 Jan 2026 missing (holiday gap), and fuel jumps
from the week of 6 Mar 2026 (VLSFO_SIN +60%, HSFO_SIN +70%, HSFO_RTM +50%, VLSFO_RTM +30%), so
three moves breach the ±40% fuel band.
