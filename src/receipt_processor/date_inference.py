from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from receipt_processor.validation import is_valid_date, parse_iso_date

_MONTH_DAY_RE = re.compile(r"(?P<month>\d{1,2})[-/](?P<day>\d{1,2})")


def resolve_receipt_date(
    value: str,
    reference_dates: Iterable[date],
    *,
    min_date: date | None = None,
    max_date: date | None = None,
) -> str | None:
    """Return a valid ISO date, inferring a missing year only from receipt context."""
    parsed = parse_iso_date(value)
    if parsed is not None:
        normalized = parsed.isoformat()
        return normalized if is_valid_date(normalized, min_date=min_date, max_date=max_date) else None

    match = _MONTH_DAY_RE.fullmatch(value.strip())
    if match is None:
        return None
    inferred = infer_yearless_date(
        int(match["month"]),
        int(match["day"]),
        reference_dates,
        min_date=min_date,
        max_date=max_date,
    )
    return inferred.isoformat() if inferred is not None else None


def infer_yearless_date(
    month: int,
    day: int,
    reference_dates: Iterable[date],
    *,
    min_date: date | None = None,
    max_date: date | None = None,
) -> date | None:
    """Infer a date from an exact month/day peer or one unambiguous batch year."""
    references = tuple(reference_dates)
    exact_matches = {item for item in references if (item.month, item.day) == (month, day)}
    if len(exact_matches) == 1:
        candidate = next(iter(exact_matches))
        return candidate if _within_bounds(candidate, min_date=min_date, max_date=max_date) else None
    if len(exact_matches) > 1:
        return None

    years = {item.year for item in references}
    if not years and min_date is not None and max_date is not None and min_date.year == max_date.year:
        years.add(min_date.year)
    if len(years) != 1:
        return None

    try:
        candidate = date(next(iter(years)), month, day)
    except ValueError:
        return None
    return candidate if _within_bounds(candidate, min_date=min_date, max_date=max_date) else None


def _within_bounds(candidate: date, *, min_date: date | None, max_date: date | None) -> bool:
    return is_valid_date(candidate.isoformat(), min_date=min_date, max_date=max_date)
