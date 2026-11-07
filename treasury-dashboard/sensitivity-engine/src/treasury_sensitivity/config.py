"""Versioned defaults loader. Defaults ship in defaults.yaml; a deployment may layer its own file."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from .models import D

DEFAULTS_PATH = Path(__file__).with_name("defaults.yaml")
FIXTURE_DIR = Path(__file__).with_name("fixtures")


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for key, value in extra.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


@dataclass(frozen=True)
class Defaults:
    raw: dict[str, Any]

    @classmethod
    def load(cls, override: Path | None = None) -> "Defaults":
        raw = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
        if override is not None:
            raw = _merge(raw, yaml.safe_load(Path(override).read_text(encoding="utf-8")) or {})
        return cls(raw)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    @property
    def defaults_version(self) -> str:
        return self.raw["defaults_version"]

    @property
    def formula_version(self) -> str:
        return self.raw["formula_version"]

    @property
    def threshold_version(self) -> str:
        return self.raw["thresholds"]["version"]

    def dec(self, *path: str) -> Decimal | None:
        node: Any = self.raw
        for part in path:
            node = node[part]
        return None if node is None else D(node)
