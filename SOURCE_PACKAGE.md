# Private Treasury dashboard handoff

This package contains the current React frontend (source and production build), gateway, sensitivity service, cash-flow engine, news engine, tests, company inputs, current market databases and saved news exports.

Real `.env` credentials are included at the user's explicit request. Keep this archive private. Virtual environments, node_modules, caches, prior code archives and historical calculation/recommendation databases are excluded.

## Setup

Use Python 3.12 or newer and Node.js/npm. Extract the complete folder, run `setup.cmd`, then `start.cmd`. Setup creates a new local Python environment, installs the cash engine as an editable package and builds the frontend. Copied virtual environments are not portable.

The supplied current market databases and company inputs are ready for calculations; do not reimport the old CSV snapshot on top of them. New dashboard calculation history is created automatically.

Normal startup runs real news searches and model estimates before opening http://127.0.0.1:8080. Credentials are read from `news-engine-v8/.env`.

For the user's current slider-only session, run `.venv/Scripts/python.exe output/treasurer-qa/slider_only_startup.py` after setup (use `.venv-local` instead if that is the installed environment). It uses saved news, skips news preparation and blocks new news forecasts for that process only; normal startup defaults stay unchanged.

The acceptance report and slider evidence are included under `output/treasurer-qa`. Company exposure data remains illustrative; the dashboard does not execute transactions.
