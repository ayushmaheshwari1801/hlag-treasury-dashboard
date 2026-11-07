# W4 inherited Claim Types and temporary R5 cap switch

Changes remain local, uncommitted/unpushed. SPEC was updated before each behavior
change. No paid model call was made. Public Event/RunResult remains contract6.

## Input-only Claim Type

W4 receives every canonical what_changed entry including claim_type and evidence.
The prompt explicitly forbids reclassification and repeated Claim Type output.
AnalysisDraft removes primary_claim_type, replacing it with primary_entry_index:
a strict zero-based input-array index. allowed_values lists the valid indices.
The selected source and quote must belong to that exact entry's primary/supporting
evidence. Python inherits its Claim Type; W4 never supplies an override. Selector
metadata is transient and does not introduce a persistent entry ID/entity.

This removes the redundant model-vs-W3 type equality failure, while preserving
source/quote selection. W3 classification mistakes are not corrected by W4. The
previous real Event E059e1d662fa411a993ed was checked read-only: W4 input retains
forecast/actual and indices0/1. Before the temporary cap-off change, selecting
actual index1 permitted P2/R3/U2=47; selecting forecast index0 still applied P<=1.
These are offline validator checks, not new model predictions or DB updates.

## Temporarily disabled hard caps

models.py has one switch: R5_CAPS_ENABLED=False. While False, Python skips basis
ceilings, proposed/forecast impact caps, R0/R1/R<=2 relationships and U>2 action gating.
Strict integer0..5 dimensions, exact total=8P+7R+5U, enums, selected-entry evidence
and nonempty explanations remain enforced. Scores are never clamped.

r5_policy_guidance() uses the same central flag/maps. Current single/batch prompts
explicitly disable hard caps and score dimensions independently; descriptive anchors
are advisory. Old mandatory Caps paragraphs/<= rules were removed from the base
rubric. Set the flag True and restart to restore Python caps and generated prompt
rules. Existing policy fingerprints still require a fresh DB after policy changes;
no old DB, original evidence, label or historical result was rewritten.

## Verification

Complete offline suite91 passed in33.627s. Focused Event51 and batch19 passed;
existing group/consolidation16 and semantic-evaluation5 also pass in the full suite.
The earlier input-only Claim Type implementation passed87 before the cap-off steering.

Regression coverage: types retained in input and absent from output; rejection of
obsolete duplicate output fields; strict/in-range indices; same-Article wrong-entry
quotes; identical quotes with different types; supporting-source scoring; old caps
restored via one flag; former restricted tuples accepted with caps off; invalid
range/formula/enums still rejected; effective prompt cap mode; batch/parallel,
source isolation, restart, long-input passthrough and numeric checks remaining off.

Active single/batch prompt checks and git diff --check passed. Existing Starlette/
httpx TestClient deprecation warning remains. Full logs:
outputs/r5-caps-disabled-offline-20261101/offline.log and summary.json.

No new live accuracy, score distribution, latency or provider-cost measurement.
