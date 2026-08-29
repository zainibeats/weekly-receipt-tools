from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from receipt_processor.aggregation import aggregate_daily_totals
from receipt_processor.date_inference import resolve_receipt_date
from receipt_processor.models import ExtractedReceipt, ProcessingFailure
from receipt_processor.ocr import OCRExtractor
from receipt_processor.ocr_parser import parse_ocr_receipt
from receipt_processor.validation import is_valid_receipt, parse_iso_date
from receipt_processor.vision_llm import VisionExtraction, VisionExtractor

IMAGE_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass(frozen=True)
class _VisionAttempt:
    """One vision backend call, held until batch date inference can run."""

    path: Path
    extraction: VisionExtraction | None = None
    failure: str | None = None


def process_directory(
    input_dir: Path,
    vision_extractor: VisionExtractor,
    *,
    max_total: float = 1000.0,
    min_date: date | None = None,
    max_date: date | None = None,
    ocr_extractor: OCRExtractor | None = None,
) -> tuple[dict[str, float], list[ExtractedReceipt], list[ProcessingFailure]]:
    """Process receipt images and return daily totals, accepted receipts, and failures."""
    # Every image is read first so yearless dates can borrow a year from the batch.
    attempts = [_extract_with_vision(path, vision_extractor) for path in iter_image_files(input_dir)]
    reference_dates = _collect_reference_dates(
        attempts,
        max_total=max_total,
        min_date=min_date,
        max_date=max_date,
    )

    receipts: list[ExtractedReceipt] = []
    failures: list[ProcessingFailure] = []
    for attempt in attempts:
        result = _resolve_attempt(
            attempt,
            ocr_extractor,
            reference_dates=reference_dates,
            max_total=max_total,
            min_date=min_date,
            max_date=max_date,
        )
        if isinstance(result, ExtractedReceipt):
            receipts.append(result)
        else:
            failures.append(result)

    return aggregate_daily_totals(receipts), receipts, failures


def _extract_with_vision(image_path: Path, vision_extractor: VisionExtractor) -> _VisionAttempt:
    """Run the vision backend for one image, keeping backend errors as review reasons."""
    try:
        extraction = vision_extractor.extract(image_path)
    except Exception as exc:
        return _VisionAttempt(image_path, failure=f"Vision extraction failed: {exc}")
    if extraction is None:
        return _VisionAttempt(image_path, failure="Vision model did not return valid JSON")
    return _VisionAttempt(image_path, extraction=extraction)


def _resolve_attempt(
    attempt: _VisionAttempt,
    ocr_extractor: OCRExtractor | None,
    *,
    reference_dates: tuple[date, ...],
    max_total: float,
    min_date: date | None,
    max_date: date | None,
) -> ExtractedReceipt | ProcessingFailure:
    """Accept a vision result, fall back to OCR, or report why the image needs review."""
    if attempt.extraction is None:
        return _needs_review(
            attempt.path,
            attempt.failure or "Vision model did not return valid JSON",
            ocr_extractor,
            reference_dates=reference_dates,
            max_total=max_total,
            min_date=min_date,
            max_date=max_date,
        )

    resolved_date = resolve_receipt_date(
        attempt.extraction.date,
        reference_dates,
        min_date=min_date,
        max_date=max_date,
    )
    if resolved_date is not None and is_valid_receipt(
        resolved_date,
        attempt.extraction.total,
        max_total=max_total,
        min_date=min_date,
        max_date=max_date,
    ):
        return _accepted_receipt(
            attempt.path,
            resolved_date,
            attempt.extraction.total,
            attempt.extraction.confidence,
            "vision_llm",
        )

    return _needs_review(
        attempt.path,
        "Vision model returned invalid date or total",
        ocr_extractor,
        reference_dates=reference_dates,
        max_total=max_total,
        min_date=min_date,
        max_date=max_date,
    )


def _needs_review(
    image_path: Path,
    vision_failure: str,
    ocr_extractor: OCRExtractor | None,
    *,
    reference_dates: tuple[date, ...],
    max_total: float,
    min_date: date | None,
    max_date: date | None,
) -> ExtractedReceipt | ProcessingFailure:
    """Try the optional OCR fallback before giving up on an image."""
    if ocr_extractor is None:
        return ProcessingFailure.from_path(image_path, vision_failure)

    fallback = _run_ocr_fallback(
        image_path,
        ocr_extractor,
        max_total=max_total,
        min_date=min_date,
        max_date=max_date,
        reference_dates=reference_dates,
    )
    if isinstance(fallback, ExtractedReceipt):
        return fallback
    return ProcessingFailure.from_path(image_path, f"{vision_failure}; {fallback}")


def _collect_reference_dates(
    attempts: list[_VisionAttempt],
    *,
    max_total: float,
    min_date: date | None,
    max_date: date | None,
) -> tuple[date, ...]:
    """Return fully dated, otherwise valid vision receipts for batch inference."""
    references: list[date] = []
    for attempt in attempts:
        extraction = attempt.extraction
        if extraction is None or not is_valid_receipt(
            extraction.date,
            extraction.total,
            max_total=max_total,
            min_date=min_date,
            max_date=max_date,
        ):
            continue
        parsed = parse_iso_date(extraction.date)
        if parsed is not None:
            references.append(parsed)
    return tuple(references)


def _run_ocr_fallback(
    image_path: Path,
    ocr_extractor: OCRExtractor,
    *,
    max_total: float,
    min_date: date | None,
    max_date: date | None,
    reference_dates: tuple[date, ...],
) -> ExtractedReceipt | str:
    """Return an OCR receipt or the reason OCR could not produce one."""
    try:
        variants = ocr_extractor.extract(image_path)
    except Exception as exc:
        return f"OCR fallback failed: {exc}"

    parsed = parse_ocr_receipt(
        variants,
        max_total=max_total,
        min_date=min_date,
        max_date=max_date,
        reference_dates=reference_dates,
    )
    if parsed.extraction is None:
        return parsed.reason
    return _accepted_receipt(
        image_path,
        parsed.extraction.date,
        parsed.extraction.total,
        parsed.extraction.confidence,
        "ocr_fallback",
    )


def _accepted_receipt(
    image_path: Path,
    date_value: str,
    total: float,
    confidence: float,
    method: str,
) -> ExtractedReceipt:
    """Build an accepted receipt with the output rounding rules applied once."""
    return ExtractedReceipt(
        file=str(image_path),
        date=date_value,
        total=round(total, 2),
        confidence=round(confidence, 3),
        method=method,
    )


def iter_image_files(input_dir: Path) -> list[Path]:
    """Return supported image files in deterministic order."""
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
