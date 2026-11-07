"""Shared base model and field-category helpers for all contracts."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    """Base for every contract: unknown fields are rejected, values are validated on assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FieldCategory(StrEnum):
    """How a ScenarioParams field behaves under zero_shock() (spec 2.7, D28)."""

    SHOCK = "shock"  # reset to its default by zero_shock()
    STATE = "state"  # identical in both passes; kept by zero_shock()
    META = "meta"  # common descriptive block; kept by zero_shock()
    BLOCK = "block"  # a nested driver block whose own fields are tagged


def tagged(category: FieldCategory, default: Any = ..., **kwargs: Any) -> Any:
    """A pydantic Field carrying its shock/state/meta category."""
    return Field(default, json_schema_extra={"category": category.value}, **kwargs)


def tagged_factory(category: FieldCategory, factory: Any, **kwargs: Any) -> Any:
    """Like tagged(), with a default factory."""
    return Field(default_factory=factory, json_schema_extra={"category": category.value}, **kwargs)


def field_category(model: type[BaseModel], name: str) -> FieldCategory | None:
    """Return the category tag of a model field, or None if untagged."""
    extra = model.model_fields[name].json_schema_extra
    if isinstance(extra, dict) and "category" in extra:
        return FieldCategory(extra["category"])
    return None
