"""hlag_engine: the deterministic treasury cash-flow stress engine (docs/spec.md).

Every config and data file is located relative to this package, never the working directory (P88).
"""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent  # the checkout holding .env, .env.example and .gitignore
