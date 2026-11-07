"""Profile access for the generator: resolves at T0 and records missing placeholders."""

from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

from hlag_engine.contracts.enums import ProvenanceLabel
from hlag_engine.contracts.exposure import MissingInput
from hlag_engine.company_profile.resolve import RunContext, resolve, resolve_detail
from hlag_engine.company_profile.schema import MissingProfileValue

T = TypeVar("T")


class Inputs:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        self.missing: list[MissingInput] = []

    @property
    def as_of(self) -> date:
        return self.ctx.as_of

    def get(self, key: str, on: date | None = None) -> Any:
        return resolve(key, on or self.ctx.as_of, self.ctx)

    def label(self, key: str) -> ProvenanceLabel:
        return resolve_detail(key, self.ctx.as_of, self.ctx).label

    def has(self, key: str) -> bool:
        return key in self.ctx.profile.parameters

    def attempt(self, affects: str, build: Callable[[], list[T]]) -> list[T]:
        """Run one recipe part; on a placeholder, record it and skip the part."""
        try:
            return build()
        except MissingProfileValue as exc:
            record = MissingInput(key=exc.key, status=exc.status, affects=affects)
            if record not in self.missing:
                self.missing.append(record)
            return []
