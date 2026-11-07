# Contract 8 implementation

Read SPEC.md first. Five stages: Search -> Article Grouping -> Event Classification
& Consolidation -> Scoring & Analysis -> JSON. Runtime modules remain in src/news_engine.
Article owns the complete source; Event is its one primary semantic label. SQLite
article_event enforces immutable unique assignment, and Python checks exact coverage
and cardinality for the evaluated set. Pending group proposals are technical metadata.
W4 reads original Articles directly. NUMERIC_GROUNDING_ENABLED=False disables
factual-number source checks; strict R5 range/arithmetic and structures remain.
No entry construction, Claim Types, numeric extraction, novelty or progression layer.

Incompatible v7 execution tests are preserved in evaluation/legacy_contract7_tests.
Historical corpora/results/labels/databases remain intact. Default v8 database is fresh.
See docs/contract8-results.md for offline evidence and limitations.
