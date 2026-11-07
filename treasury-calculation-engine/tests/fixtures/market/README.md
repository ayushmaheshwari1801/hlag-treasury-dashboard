# Recorded market data responses (tests only)

Tests never call the internet; connectors are tested against these files through
`httpx.MockTransport`. Window: 29 Dec 2025 – 6 Jan 2026 (covers the 31 Dec 2025 lock-rate date
and the 1 Jan holiday gap).

| File | Recorded from (2 Nov 2026) |
| :--- | :--- |
| `ecb_exr.csv` | `https://data-api.ecb.europa.eu/service/data/EXR/D.USD+CAD+GBP+CNY+INR+BRL.EUR.SP00.A?startPeriod=2025-12-29&endPeriod=2026-01-06&format=csvdata` |
| `ecb_estr.csv` | `.../EST/B.EU000A2X2A25.WT?...` |
| `ecb_ea2y.csv` | `.../YC/B.U2.EUR.4F.G_N_A.SV_C_YM.SR_2Y?...` |
| `nyfed_sofr.json` | `https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2025-12-29&endDate=2026-01-06` |
| `fred_csv_<ID>.csv` | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<ID>&cosd=2025-12-29&coed=2026-01-06` |
| `frankfurter.json` | `https://api.frankfurter.dev/v1/2025-12-29..2026-01-06?base=EUR&symbols=USD,CAD,GBP,CNY,INR,BRL` |
| `fred_api_DGS2.json` | Not recorded (needs an API key): written by hand in the documented FRED API `series/observations` JSON format, with the same values as `fred_csv_DGS2.csv` and FRED's `.` missing marker |
