from datetime import date

from receipt_processor.ocr import OCRLine
from receipt_processor.ocr_parser import parse_ocr_receipt


def _parse(*lines: str, reference_date: date = date(2026, 7, 14)):
    variant = tuple(OCRLine(text=line, confidence=0.9) for line in lines)
    return parse_ocr_receipt((variant,), reference_date=reference_date)


def test_parser_completes_month_day_with_reference_year() -> None:
    result = _parse("DATE 07/09", "TOTAL $23.76")

    assert result.extraction is not None
    assert result.extraction.date == "2026-07-09"
    assert result.extraction.total == 23.76


def test_parser_does_not_use_local_year_without_receipt_context() -> None:
    variant = (
        OCRLine(text="DATE 07/09", confidence=0.9),
        OCRLine(text="TOTAL 23.76", confidence=0.9),
    )

    result = parse_ocr_receipt((variant,))

    assert result.extraction is None
    assert result.reason == "OCR candidates were ambiguous: no valid date"


def test_parser_prefers_exact_month_day_reference() -> None:
    variant = (
        OCRLine(text="DATE 07/14", confidence=0.9),
        OCRLine(text="TOTAL 23.76", confidence=0.9),
    )

    result = parse_ocr_receipt(
        (variant,),
        reference_dates=(date(2025, 7, 13), date(2026, 7, 14), date(2025, 7, 15)),
    )

    assert result.extraction is not None
    assert result.extraction.date == "2026-07-14"


def test_parser_retains_full_date_year() -> None:
    result = _parse("DATE 12/31/2025", "AMOUNT 14.25")

    assert result.extraction is not None
    assert result.extraction.date == "2025-12-31"


def test_parser_prefers_labeled_total() -> None:
    result = _parse(
        "07/09/2026",
        "SUBTOTAL 20.00",
        "TAX 3.76",
        "TOTAL 23.76",
        "CASH 30.00",
        "CHANGE 6.24",
    )

    assert result.extraction is not None
    assert result.extraction.total == 23.76


def test_parser_ignores_identifiers_and_uses_unique_unlabeled_amount() -> None:
    result = _parse(
        "DATE 07/09",
        "PHONE 555.123.4567",
        "TICKET 123.45",
        "ORDER 987.65",
        "$23.76",
    )

    assert result.extraction is not None
    assert result.extraction.total == 23.76


def test_parser_rejects_ambiguous_dates() -> None:
    result = _parse("07/09", "07/10", "TOTAL 23.76")

    assert result.extraction is None
    assert result.reason == "OCR candidates were ambiguous: ambiguous date candidates"


def test_parser_rejects_ambiguous_unlabeled_totals() -> None:
    result = _parse("DATE 07/09", "$20.00", "$23.76")

    assert result.extraction is None
    assert result.reason == "OCR candidates were ambiguous: ambiguous total candidates"


def test_parser_applies_date_and_total_bounds() -> None:
    variant = (
        OCRLine(text="DATE 07/09/2026", confidence=0.9),
        OCRLine(text="TOTAL 23.76", confidence=0.9),
    )

    date_result = parse_ocr_receipt((variant,), min_date=date(2026, 7, 10))
    total_result = parse_ocr_receipt((variant,), max_total=20.0)

    assert date_result.extraction is None
    assert "no valid date" in date_result.reason
    assert total_result.extraction is None
    assert "no valid final total" in total_result.reason


def test_parser_deduplicates_candidates_across_variants() -> None:
    variant = (
        OCRLine(text="DATE 07/09", confidence=0.8),
        OCRLine(text="TOTAL 23.76", confidence=0.8),
    )
    enhanced = (
        OCRLine(text="DATE 07/09", confidence=0.95),
        OCRLine(text="TOTAL 23.76", confidence=0.95),
    )

    result = parse_ocr_receipt((variant, enhanced), reference_date=date(2026, 7, 14))

    assert result.extraction is not None
    assert result.extraction.confidence == 0.95


def test_parser_accepts_sample_receipt_ocr_line() -> None:
    result = _parse("033648 12:55 023.76 07/09 18:24 00")

    assert result.extraction is not None
    assert result.extraction.date == "2026-07-09"
    assert result.extraction.total == 23.76
