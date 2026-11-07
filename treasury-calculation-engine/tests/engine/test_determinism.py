"""The engine and everything it calls is deterministic: no LLM, network or randomness (CLAUDE.md, 1.3)."""

import ast
from pathlib import Path

from hlag_engine import PACKAGE_DIR as ROOT
FORBIDDEN = {"random", "secrets", "uuid", "httpx", "requests", "urllib", "socket", "anthropic", "openai", "numpy.random"}


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_engine_modules_generator_and_profile_import_nothing_forbidden():
    for package in ("engine", "modules", "generator", "company_profile", "contracts"):
        paths = sorted((ROOT / package).rglob("*.py"))
        assert paths, f"no Python files found under {ROOT / package}"
        for path in paths:
            bad = {n for n in imports(path) if n.split(".")[0] in FORBIDDEN or n in FORBIDDEN}
            assert not bad, f"{path.relative_to(ROOT)} imports {bad}"
