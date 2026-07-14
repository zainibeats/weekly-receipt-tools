from __future__ import annotations

from datetime import date
from pathlib import Path

from receipt_processor.aggregation import aggregate_daily_totals
from receipt_processor.models import ExtractedReceipt, ProcessingFailure
from receipt_processor.ocr import OCRExtractor
from receipt_processor.ocr_parser import parse_ocr_receipt
from receipt_processor.validation import is_valid_receipt
from receipt_processor.vision_llm import VisionExtractor

IMAGE_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


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
    receipts: list[ExtractedReceipt] = []
    failures: list[ProcessingFailure] = []

    for image_path in iter_image_files(input_dir):
        vision_failure: str | None = None
        try:
            extraction = vision_extractor.extract(image_path)
        except Exception as exc:
            extraction = None
            vision_failure = f"Vision extraction failed: {exc}"

        if extraction is None:
            vision_failure = vision_failure or "Vision model did not return valid JSON"
        elif not is_valid_receipt(
            extraction.date,
            extraction.total,
            max_total=max_total,
            min_date=min_date,
            max_date=max_date,
        ):
            vision_failure = "Vision model returned invalid date or total"

        if vision_failure is not None:
            fallback = _run_ocr_fallback(
                image_path,
                ocr_extractor,
                max_total=max_total,
                min_date=min_date,
                max_date=max_date,
            )
            if fallback is None:
                failures.append(ProcessingFailure.from_path(image_path, vision_failure))
                continue
            if isinstance(fallback, str):
                failures.append(ProcessingFailure.from_path(image_path, f"{vision_failure}; {fallback}"))
                continue
            receipts.append(fallback)
            continue

        receipts.append(
            ExtractedReceipt(
                file=str(image_path),
                date=extraction.date,
                total=round(extraction.total, 2),
                confidence=round(extraction.confidence, 3),
                method="vision_llm",
            )
        )

    return aggregate_daily_totals(receipts), receipts, failures


def _run_ocr_fallback(
    image_path: Path,
    ocr_extractor: OCRExtractor | None,
    *,
    max_total: float,
    min_date: date | None,
    max_date: date | None,
) -> ExtractedReceipt | str | None:
    """Return an OCR receipt, a failure detail, or None when disabled."""
    if ocr_extractor is None:
        return None
    try:
        variants = ocr_extractor.extract(image_path)
    except Exception as exc:
        return f"OCR fallback failed: {exc}"

    parsed = parse_ocr_receipt(
        variants,
        max_total=max_total,
        min_date=min_date,
        max_date=max_date,
    )
    if parsed.extraction is None:
        return parsed.reason
    extraction = parsed.extraction
    return ExtractedReceipt(
        file=str(image_path),
        date=extraction.date,
        total=round(extraction.total, 2),
        confidence=round(extraction.confidence, 3),
        method="ocr_fallback",
    )


def iter_image_files(input_dir: Path) -> list[Path]:
    """Return supported image files in deterministic order."""
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
