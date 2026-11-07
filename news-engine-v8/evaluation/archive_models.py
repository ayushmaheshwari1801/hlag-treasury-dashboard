"""Read-only validation for immutable historical evaluation data, never runtime output."""
import math
from typing import Annotated, Literal, Self
from pydantic import AfterValidator, BeforeValidator, BaseModel, ConfigDict, Field, model_validator

def _nonempty(value):
    if not value.strip(): raise ValueError('Text must not be empty')
    return value

def _finite_number(value):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError('A number must be finite and cannot be a boolean')
    return value

NonEmptyText=Annotated[str,AfterValidator(_nonempty)]
FiniteNumber=Annotated[int|float,BeforeValidator(_finite_number)]
Direction=Literal['increase','decrease','stable','tightening','easing','worsening','improving']

class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False,revalidate_instances='always')

class Change(StrictModel):
    previous: FiniteNumber
    delta: FiniteNumber
    unit: NonEmptyText

class Signal(StrictModel):
    indicator: NonEmptyText
    level: Literal["numeric", "directional", "factual_only"]
    value: FiniteNumber | None
    unit: NonEmptyText | None
    change: Change | None
    direction: Direction | None
    source_ids: Annotated[list[NonEmptyText], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_level(self) -> Self:
        if self.level == "numeric":
            if self.value is None or self.unit is None:
                raise ValueError("Numeric signals require a value and unit")
        else:
            if any(value is not None for value in (self.value, self.unit, self.change)):
                raise ValueError("Non-numeric signals require null value, unit and change")
            if self.level == "directional" and self.direction is None:
                raise ValueError("Directional signals require a direction")
            if self.level == "factual_only" and self.direction is not None:
                raise ValueError("Factual-only signals require a null direction")
        return self
