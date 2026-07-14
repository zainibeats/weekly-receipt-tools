from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from receipt_processor.ocr import OCRLine, OCRVariant
from receipt_processor.validation import is_valid_date, is_valid_total

_ISO_DATE_RE = re.compile(r"(?<!\d)(?P<year>20\d{2})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)")
_US_DATE_RE = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?:[-/](?P<year>\d{2}|20\d{2}))?(?!\d)"
)
_AMOUNT_RE = re.compile(r"(?<![\d/])(?P<currency>\$)?\s*(?P<amount>\d+(?:,\d{3})*\.\d{2})(?!\d)")
_TOTAL_LABEL_RE = re.compile(r"\b(total|amount|charged|paid)\b", re.IGNORECASE)
_NON_TOTAL_LABEL_RE = re.compile(
    r"\b(sub[ -]?total|tax|cash|change|tender|tip|phone|address|ticket|order|invoice)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OCRReceiptExtraction:
    """Receipt fields selected from deterministic OCR candidates."""

    date: str
    total: float
    confidence: float


@dataclass(frozen=True)
class OCRParseResult:
    """An accepted OCR extraction or a review reason."""

    extraction: OCRReceiptExtraction | None
    reason: str


@dataclass(frozen=True)
class _Candidate:
    value: str | float
    confidence: float


def parse_ocr_receipt(
    variants: tuple[OCRVariant, ...],
    *,
    reference_date: date | None = None,
    max_total: float = 1000.0,
    min_date: date | None = None,
    max_date: date | None = None,
) -> OCRParseResult:
    """Select one valid date and one unambiguous final total from OCR text."""
    lines = [line for variant in variants for line in variant if line.text.strip()]
    if not lines:
        return OCRParseResult(None, "OCR found no usable text")

    reference_date = reference_date or date.today()
    dates = _date_candidates(lines, reference_date, min_date=min_date, max_date=max_date)
    if len(dates) != 1:
        detail = "no valid date" if not dates else "ambiguous date candidates"
        return OCRParseResult(None, f"OCR candidates were ambiguous: {detail}")

    totals = _total_candidates(lines, max_total=max_total)
    if len(totals) != 1:
        detail = "no valid final total" if not totals else "ambiguous total candidates"
        return OCRParseResult(None, f"OCR candidates were ambiguous: {detail}")

    date_candidate = next(iter(dates.values()))
    total_candidate = next(iter(totals.values()))
    return OCRParseResult(
        OCRReceiptExtraction(
            date=str(date_candidate.value),
            total=float(total_candidate.value),
            confidence=min(date_candidate.confidence, total_candidate.confidence),
        ),
        "",
    )


def _date_candidates(
    lines: list[OCRLine],
    reference_date: date,
    *,
    min_date: date | None,
    max_date: date | None,
) -> dict[str, _Candidate]:
    candidates: dict[str, _Candidate] = {}
    for line in lines:
        spans: list[tuple[int, int]] = []
        for match in _ISO_DATE_RE.finditer(line.text):
            spans.append(match.span())
            _add_date_candidate(
                candidates,
                int(match["year"]),
                int(match["month"]),
                int(match["day"]),
                line.confidence,
                min_date=min_date,
                max_date=max_date,
            )
        for match in _US_DATE_RE.finditer(line.text):
            if any(_overlaps(match.span(), span) for span in spans):
                continue
            raw_year = match["year"]
            year = reference_date.year if raw_year is None else _normalize_year(raw_year)
            _add_date_candidate(
                candidates,
                year,
                int(match["month"]),
                int(match["day"]),
                line.confidence,
                min_date=min_date,
                max_date=max_date,
            )
    return candidates


def _add_date_candidate(
    candidates: dict[str, _Candidate],
    year: int,
    month: int,
    day: int,
    confidence: float,
    *,
    min_date: date | None,
    max_date: date | None,
) -> None:
    try:
        value = date(year, month, day).isoformat()
    except ValueError:
        return
    if is_valid_date(value, min_date=min_date, max_date=max_date):
        _keep_best(candidates, value, confidence)


def _total_candidates(lines: list[OCRLine], *, max_total: float) -> dict[float, _Candidate]:
    labeled: dict[float, _Candidate] = {}
    unlabeled: dict[float, _Candidate] = {}
    for line in lines:
        if _NON_TOTAL_LABEL_RE.search(line.text):
            continue
        destination = labeled if _TOTAL_LABEL_RE.search(line.text) else unlabeled
        for match in _AMOUNT_RE.finditer(line.text):
            value = float(match["amount"].replace(",", ""))
            if is_valid_total(value, max_total=max_total):
                _keep_best(destination, value, line.confidence)
    return labeled or unlabeled


def _keep_best(candidates: dict, value: str | float, confidence: float) -> None:
    current = candidates.get(value)
    if current is None or confidence > current.confidence:
        candidates[value] = _Candidate(value, confidence)


def _normalize_year(value: str) -> int:
    year = int(value)
    return 2000 + year if len(value) == 2 else year


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]
