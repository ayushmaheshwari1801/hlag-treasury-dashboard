# Integration validation

## Recommendation agent continuation — 7 November 2026

The existing hedge agent panel, API, deterministic hedge tools and session history are present. This continuation binds saved-forecast recommendations to their immutable news snapshot, rejects backdating and missing snapshots, validates malformed model recommendation objects, and corrects the rule planner's risk attribution wording.

Offline validation: 17 recommendation-agent tests passed; 43 forecast, cumulative-news and gateway regression tests passed; all 48 frontend tests passed; TypeScript and the Vite production build passed. Provider calls were mocked. FastAPI emitted an existing TestClient/httpx deprecation warning.

Checks used the available Python 3.12 environment at `E:/YTP-AIF/demo-version/.venv` and local source paths. The bundled `.venv` still points to another computer's Python installation. Live model execution and browser interaction were not verified in this continuation.

## Previous integration validation

Validated locally on 2 November 2026.

| Check | Result |
|---|---|
| Gateway regression suite after original-page and six-Event integration | 78 passed |
| Earlier full gateway and sensitivity backend regression suite | 318 passed |
| Original news engine v8 offline benchmark | 80 passed |
| Shared market feed unit tests | 6 passed |
| Frontend forecast, grouping, attribution and Event-scenario tests | 8 passed |
| TypeScript and production Vite build | Passed |
| HTTP frontend, OpenAPI and cash engine smoke checks | HTTP 200 |

The end-to-end tests import v8 evidence, infer a rule-based forecast, apply it to the cash engine,
verify a changed liquidity path, preserve the original forecast, record user overrides, and verify
that neither the profile nor approved adjustments changed. Model responses are mocked in offline
tests; no paid model or search calls were made.

The updated local service loads the versioned manual override over the verified official financial
profile, six selected v8 Events with eleven original Articles, and twenty of twenty market series.
The six scenarios are exactly two regulatory, two macroeconomics and two industry Events. The
original webpage is the sole entry point; each Event is forecast and applied independently.
The local HTTP smoke check returned no missing calculation inputs. This completeness includes
the user-authorized CSV assumptions, not newly observed market histories or bank data. Their
sources and limitations are recorded in `../../inputs/manual-assumptions.md`. Saved news selection
provenance is in `../inputs/news-scenario-selection.md`.

This validation does not establish forecast accuracy, browser visual quality, a live company
position, or complete acceptance of every item in the long prototype specification. Browser
visual verification and live model execution were not completed. See the implementation and
financial source audit for the remaining limitations.
