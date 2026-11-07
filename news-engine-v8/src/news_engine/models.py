"""Contract 8: immutable source Articles and semantic Event labels."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class ArticleInput(StrictModel):
    url: str
    title: str
    text: str = Field(min_length=1)
    publisher: str | None = None
    published_at: AwareDatetime | None = None

class Article(ArticleInput):
    article_id: str = Field(pattern=r'^A[A-Za-z0-9_-]+$')
    discovered_at: AwareDatetime
    normalized_url: str
    content_hash: str

IMPACT_CAPS = {'none':0, 'commentary_or_forecast':1, 'benchmark_or_operating':2,
               'financial_action':3, 'constrained_access':4, 'lost_capability':5}
RELEVANCE_CAPS = {'none':0, 'speculative':1, 'indirect_or_watch':2,
                  'local_unproven':3, 'direct_channel':4, 'named_company':5}
URGENCY_CAPS = {'none':0, 'long_horizon':1, 'routine_or_unknown':2,
                'operative_control_or_8_30_days':3, 'due_3_7_days':4,
                'due_0_2_days_or_blocked':5}

R5_CAPS_ENABLED = False
NUMERIC_GROUNDING_ENABLED = False


def validate_r5(impact, relevance, urgency, *, total=None, impact_basis=None,
                relevance_basis=None, urgency_basis=None,
                has_treasury_action_or_constraint=None):
    """The single R5 constraint/arithmetic authority; never coerce invalid scores."""
    values = (impact, relevance, urgency)
    if any(type(value) is not int or not 0 <= value <= 5 for value in values):
        raise ValueError('R5 dimensions must be strict integers from 0 to 5')
    for label, value, basis, caps in (
            ('impact', impact, impact_basis, IMPACT_CAPS),
            ('relevance', relevance, relevance_basis, RELEVANCE_CAPS),
            ('urgency', urgency, urgency_basis, URGENCY_CAPS)):
        if basis is not None:
            if basis not in caps:raise ValueError(f'Unknown R5 {label} basis')
            if R5_CAPS_ENABLED and value > caps[basis]:
                raise ValueError(f'R5 {label}={value} exceeds the declared {basis} anchor/cap')
    if R5_CAPS_ENABLED:
        if relevance == 0 and (impact or urgency):
            raise ValueError('R5 R=0 requires P=0 and U=0')
        if relevance == 1 and (impact > 1 or urgency > 1):
            raise ValueError('R5 R=1 caps P and U at 1')
        if relevance <= 2 and urgency > 2:
            raise ValueError('R5 R<=2 caps U at 2')
        if has_treasury_action_or_constraint is not None and urgency > 2 and not has_treasury_action_or_constraint:
            raise ValueError('R5 U>2 requires a concrete Treasury action/access/control constraint')
    calculated = 8*impact + 7*relevance + 5*urgency
    if total is not None and (type(total) is not int or total != calculated):
        raise ValueError('R5 total must equal 8P + 7R + 5U')
    return calculated


class Score(StrictModel):
    impact: int = Field(strict=True,ge=0,le=5)
    relevance: int = Field(strict=True,ge=0,le=5)
    urgency: int = Field(strict=True,ge=0,le=5)
    total: int = Field(strict=True, ge=0, le=100)
    reasoning: dict[str,str]

    @model_validator(mode='after')
    def validate_score(self):
        validate_r5(self.impact, self.relevance, self.urgency, total=self.total)
        if set(self.reasoning)!={'impact','relevance','urgency'} or any(not isinstance(v,str) or not v.strip() for v in self.reasoning.values()):
            raise ValueError('Score requires impact/relevance/urgency explanations')
        return self


Category = Literal["regulatory", "macroeconomics", "industry"]

class Event(StrictModel):
    event_id: str = Field(pattern=r'^E[A-Za-z0-9_-]+$')
    title: str = Field(min_length=1)
    category: Category
    article_ids: list[str] = Field(min_length=1)
    summary: str = Field(min_length=1)
    score: Score | None = None
    why_it_matters: list[str] = Field(default_factory=list, max_length=2)
    what_to_do: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def validate_event(self):
        import re
        if not self.title.strip() or not self.summary.strip():
            raise ValueError('Event title and summary must be nonempty')
        if len(self.article_ids) != len(set(self.article_ids)):
            raise ValueError('Event Article IDs must be unique')
        if any(not re.fullmatch(r'A[A-Za-z0-9_-]+', key) for key in self.article_ids):
            raise ValueError('Invalid Event Article ID')
        if any(not text.strip() for text in self.why_it_matters + self.what_to_do):
            raise ValueError('Analysis entries must be nonempty')
        return self

class Error(StrictModel):
    stage: str
    item_id: str | None = None
    code: str
    message: str


def validate_assignments(events, evaluated_article_ids, *, require_complete=True):
    """Validate current evaluated assignments, never unrelated historical counts."""
    evaluated = set(evaluated_article_ids)
    owners = {}
    event_ids = set()
    for event in events:
        if event.event_id in event_ids:
            raise ValueError('Duplicate Event ID')
        event_ids.add(event.event_id)
        if not event.article_ids:
            raise ValueError('Every Event requires an Article')
        for article_id in event.article_ids:
            if article_id in owners:
                raise ValueError('An Article cannot belong to multiple Events')
            owners[article_id] = event.event_id
    missing = evaluated - owners.keys()
    if require_complete and missing:
        raise ValueError('Every relevant Article requires exactly one Event assignment')
    relevant_events = {owners[key] for key in evaluated if key in owners}
    if len(relevant_events) > len(evaluated):
        raise ValueError('Critical cardinality regression: Events exceed relevant Articles')
    return {'unique_relevant_articles': len(evaluated), 'unique_events': len(relevant_events)}


class RunResult(StrictModel):
    schema_version: Literal[8] = 8
    run_id: str
    status: Literal['completed','partial','failed']
    events: list[Event] = Field(default_factory=list)
    sources: list[Article] = Field(default_factory=list)
    errors: list[Error] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    overall_summary: str = ""

    @model_validator(mode='after')
    def validate_result(self):
        source_ids = {source.article_id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError('Duplicate Article in sources')
        associated = {key for event in self.events for key in event.article_ids}
        if not associated <= source_ids:
            raise ValueError('Event references an Article absent from sources')
        validate_assignments(self.events, associated)
        return self


def event_rank_key(event):
    score = event.score
    values = (score.total, score.relevance, score.impact, score.urgency) if score else (-1,-1,-1,-1)
    return (*(-value for value in values), event.event_id)
