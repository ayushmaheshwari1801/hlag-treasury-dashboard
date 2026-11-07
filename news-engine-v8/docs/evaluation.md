# Current evaluation

Contract 8: run uv sync --frozen (plus --extra viewer for UI coverage), then
uv run --no-sync python benchmark.py --profile offline. No paid provider calls.
See [contract 8 results](contract8-results.md) for scope, counts and limitations.
python -m evaluation.offline_classification replays unchanged authentic sources
with explicit fixed-label responses. It validates contracts, not model quality.
Historical evaluation artifacts remain preserved and must not override SPEC.md.
Live/full profiles use paid providers and require explicit authorization.
