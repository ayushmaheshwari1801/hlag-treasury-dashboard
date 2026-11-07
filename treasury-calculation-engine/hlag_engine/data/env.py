"""Reads settings such as FRED_API_KEY from the environment or a .env file (never from code).

The process environment wins over .env. Only simple KEY=value lines are read; blank lines and
lines starting with # are ignored, and surrounding quotes are removed.
"""

import os
from pathlib import Path

from hlag_engine import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT
DEFAULT_ENV_PATH = REPO_ROOT / ".env"


def read_env_file(path: Path | str = DEFAULT_ENV_PATH) -> dict[str, str]:
    path = Path(path)
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def read_env_value(name: str, path: Path | str = DEFAULT_ENV_PATH) -> str | None:
    """The value, or None when unset or empty."""
    value = os.environ.get(name)
    if value is None:
        value = read_env_file(path).get(name)
    return value or None
