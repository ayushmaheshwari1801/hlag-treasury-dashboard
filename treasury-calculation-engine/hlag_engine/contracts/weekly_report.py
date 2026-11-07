"""The news engine's weekly report format (spec 10.5, D68)."""

from datetime import date

from pydantic import Field, model_validator

from hlag_engine.contracts._base import ContractModel
from hlag_engine.contracts.enums import NewsCategory


class WeeklyReportItem(ContractModel):
    event_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class WeeklyReportSection(ContractModel):
    category: NewsCategory
    items: list[WeeklyReportItem] = Field(default_factory=list)


class WeeklyReport(ContractModel):
    week_start: date
    week_end: date
    sections: list[WeeklyReportSection] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ordered(self) -> "WeeklyReport":
        if self.week_end < self.week_start:
            raise ValueError("week_end cannot be before week_start")
        return self
