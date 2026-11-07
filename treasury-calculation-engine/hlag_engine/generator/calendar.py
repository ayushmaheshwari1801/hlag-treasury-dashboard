"""Calendar helpers for the generator (spec 2.5). Pure functions, no randomness."""

import calendar
from datetime import date, timedelta


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    day = min(d.day, calendar.monthrange(year, month + 1)[1])
    return date(year, month + 1, day)


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def horizon(as_of: date) -> tuple[date, date]:
    """T0 + 1 day to the last day of the month containing T0 + 12 months (2.5)."""
    return as_of + timedelta(days=1), month_end(add_months(as_of, 12))


def months_between(start: date, end: date) -> list[date]:
    """First day of every calendar month touching [start, end]."""
    months, current = [], first_of_month(start)
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def overlap_days(month: date, start: date, end: date) -> int:
    """Days of the given month that fall inside [start, end]."""
    lo, hi = max(first_of_month(month), start), min(month_end(month), end)
    return max(0, (hi - lo).days + 1)


def quarter_of(d: date) -> int:
    return (d.month - 1) // 3 + 1


def days_in_quarter(year: int, quarter: int) -> int:
    first = date(year, 3 * quarter - 2, 1)
    return (month_end(add_months(first, 2)) - first).days + 1


def weekdays_between(start: date, end: date, weekday: int) -> list[date]:
    """Every date with the given weekday (Monday = 0) in [start, end]."""
    current = start + timedelta(days=(weekday - start.weekday()) % 7)
    out = []
    while current <= end:
        out.append(current)
        current += timedelta(days=7)
    return out


def firsts_after(as_of: date, count: int) -> list[date]:
    """The first day of each of the next `count` months, strictly after as_of."""
    first = add_months(first_of_month(as_of), 1)
    return [add_months(first, i) for i in range(count)]


def every_n_months(first: date, n: int, end: date) -> list[date]:
    out, current = [], first
    while current <= end:
        out.append(current)
        current = add_months(current, n)
    return out
