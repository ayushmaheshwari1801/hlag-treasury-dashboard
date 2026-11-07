"""Metric result returned by get_metric (spec 4.8)."""

from pydantic import Field

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import ProvenanceLabel


class MetricResult(ContractModel):
    name: str = Field(min_length=1)
    value: float | int | bool | str | None
    unit: str
    label: ProvenanceLabel
    formula: str
    run_id: str = Field(min_length=1)
