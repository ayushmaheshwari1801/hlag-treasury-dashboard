"""Concrete ID diagnostics shared by runtime classification and matching schemas."""
import json
import re


def _safe_id(value, allowed=()):
    return isinstance(value, str) and (value in allowed or re.fullmatch(r'[AEDP][A-Za-z0-9_-]{0,127}', value))


class IDConstraintError(ValueError):
    def __init__(self, code, *, missing_ids=(), duplicate_ids=(), foreign_ids=(), **details):
        self.details = dict(code=code, missing_ids=sorted(set(missing_ids)),
                            duplicate_ids=sorted(set(duplicate_ids)),
                            foreign_ids=sorted(set(foreign_ids)), **details)
        super().__init__(json.dumps(self.details, ensure_ascii=False, sort_keys=True))


def validation_error(exc, stage, constraints=None, allowed_ids=()):
    """Return ID-only diagnostics; unrelated structural failures keep their normal path."""
    constraints = constraints or {}
    articles = set(constraints.get('article_ids', ()))
    candidates = set(constraints.get('event_ids', allowed_ids))
    issues = set(constraints.get('issue_ids', ()))
    missing, foreign, invalid, fields = set(), set(), [], []
    for error in exc.errors(include_url=False):
        loc = error['loc']
        kind = error['type']
        field = loc[-1] if loc else None
        assignment = len(loc) >= 2 and loc[0] == 'assignments'
        table = loc == ('assignments',)
        identity = assignment or table or field in {'issue_id', 'event_id'} or (loc and loc[0] == 'event_ids')
        if not identity:
            continue
        path = [part if isinstance(part, int) or part in {'assignments', 'events', 'issue_id', 'event_id', 'event_ids'} or _safe_id(part, articles | candidates | issues)
                else '[redacted]' for part in loc]
        fields.append({'path': path, 'type': kind})
        if table and kind == 'missing':
            missing.update(articles)
        elif assignment and kind == 'missing':
            if _safe_id(field, articles): missing.add(field)
        elif assignment and kind == 'extra_forbidden':
            if _safe_id(field): foreign.add(field)
        elif kind == 'literal_error':
            value = error.get('input')
            if _safe_id(value, articles | candidates | issues):
                foreign.add(value)
                if assignment:
                    invalid.append({'article_id': str(field), 'issue_id': value})
        elif assignment:
            invalid.append({'article_id': field if _safe_id(field, articles) else '[redacted]', 'error': kind})
    if not fields:
        return None
    return IDConstraintError('invalid_id_response', missing_ids=missing, foreign_ids=foreign,
        stage=stage, invalid_references=invalid, fields=fields,
        allowed_article_ids=sorted(articles), allowed_event_ids=sorted(candidates), allowed_issue_ids=sorted(issues))
