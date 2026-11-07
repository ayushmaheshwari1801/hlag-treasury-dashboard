> Historical reference only. Current runtime, commands and acceptance are defined in [SPEC.md](../../SPEC.md).

# Repository cleanup

The cleanup removed retired evaluation CLI aliases, the obsolete acceptance runner,
and superseded workflow guides. Current instructions use canonical evaluation
modules and V2 operations. Historical implementation measurements remain intact.

Collection no longer reads retired W2 run-metric contracts or imports the removed
`w2_followups` module. Current article, Watch and Event follow-up sources remain.
A regression covers collection after a saved processing run.

Disposable Python caches, source egg metadata, the stale source ZIP and the local
Matplotlib font cache were removed. The source package was rebuilt and its archive
validation passed before removing the generated ZIP.

Authentic datasets, gold labels, credentials, the virtual environment, databases,
baseline results, failed runs and final evaluation artifacts were retained.
No provider calls were made during cleanup.

Validation passed:

- Full offline benchmark: contracts and unified-offline suites.
- MajorChange dependency gate: 44 production modules import, no retired contracts.
- MajorChange model checks: 14 tests.
- Workflow and recovery checks: 16 tests.
- Provider transport checks: 14 tests.
- Source package integrity and Git whitespace checks.

The former extraction measurements (removed on request) retain unresolved
semantic quality flags. Repository cleanup does not certify model accuracy.
