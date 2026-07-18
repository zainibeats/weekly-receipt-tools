from pathlib import Path

from receipt_processor.pipeline import process_directory
from receipt_processor.ocr import OCRLine
from receipt_processor.vision_llm import VisionExtraction


class StubVisionExtractor:
    def __init__(self, results: dict[str, VisionExtraction | None]) -> None:
        self.results = results
        self.calls: list[Path] = []

    def extract(self, image_path: Path) -> VisionExtraction | None:
        self.calls.append(image_path)
        return self.results.get(image_path.name)


class StubOCRExtractor:
    def __init__(self, lines: tuple[OCRLine, ...]) -> None:
        self.lines = lines
        self.calls: list[Path] = []

    def extract(self, image_path: Path):
        self.calls.append(image_path)
        return (self.lines,)


def test_pipeline_uses_vision_llm(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "a.jpg").write_bytes(b"fake")
    (receipt_dir / "b.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor(
        {
            "a.jpg": VisionExtraction(date="2026-06-01", total=12.34, confidence=0.8),
            "b.jpg": VisionExtraction(date="2026-06-01", total=7.66, confidence=0.7),
        }
    )

    daily_totals, receipts, failures = process_directory(receipt_dir, vision)

    assert daily_totals == {"2026-06-01": 20.0}
    assert [receipt.method for receipt in receipts] == ["vision_llm", "vision_llm"]
    assert failures == []
    assert [path.name for path in vision.calls] == ["a.jpg", "b.jpg"]


def test_pipeline_resolves_yearless_vision_date_from_exact_peer(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "a.jpg").write_bytes(b"fake")
    (receipt_dir / "b.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor(
        {
            "a.jpg": VisionExtraction(date="07-14", total=12.34, confidence=0.8),
            "b.jpg": VisionExtraction(date="2026-07-14", total=7.66, confidence=0.7),
        }
    )

    daily_totals, receipts, failures = process_directory(receipt_dir, vision)

    assert daily_totals == {"2026-07-14": 20.0}
    assert [receipt.date for receipt in receipts] == ["2026-07-14", "2026-07-14"]
    assert failures == []


def test_pipeline_resolves_yearless_vision_date_from_unambiguous_batch_year(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "a.jpg").write_bytes(b"fake")
    (receipt_dir / "b.jpg").write_bytes(b"fake")
    (receipt_dir / "c.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor(
        {
            "a.jpg": VisionExtraction(date="07/14", total=10.0),
            "b.jpg": VisionExtraction(date="2026-07-13", total=10.0),
            "c.jpg": VisionExtraction(date="2026-07-15", total=10.0),
        }
    )

    daily_totals, receipts, failures = process_directory(receipt_dir, vision)

    assert daily_totals == {
        "2026-07-13": 10.0,
        "2026-07-14": 10.0,
        "2026-07-15": 10.0,
    }
    assert failures == []


def test_pipeline_includes_heic_and_heif_images(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "a.HEIC").write_bytes(b"fake")
    (receipt_dir / "b.heif").write_bytes(b"fake")
    (receipt_dir / "c.txt").write_text("not an image", encoding="utf-8")
    vision = StubVisionExtractor(
        {
            "a.HEIC": VisionExtraction(date="2026-06-01", total=12.34, confidence=0.8),
            "b.heif": VisionExtraction(date="2026-06-02", total=7.66, confidence=0.7),
        }
    )

    daily_totals, receipts, failures = process_directory(receipt_dir, vision)

    assert daily_totals == {"2026-06-01": 12.34, "2026-06-02": 7.66}
    assert [receipt.file for receipt in receipts] == [
        str(receipt_dir / "a.HEIC"),
        str(receipt_dir / "b.heif"),
    ]
    assert failures == []
    assert [path.name for path in vision.calls] == ["a.HEIC", "b.heif"]


def test_pipeline_rejects_invalid_vision_results(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "bad.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor({"bad.jpg": VisionExtraction(date="not-a-date", total=12.34)})

    daily_totals, receipts, failures = process_directory(receipt_dir, vision)

    assert daily_totals == {}
    assert receipts == []
    assert len(failures) == 1


def test_pipeline_does_not_call_ocr_after_valid_vision_result(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "good.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor({"good.jpg": VisionExtraction(date="2026-06-01", total=12.34)})
    ocr = StubOCRExtractor(())

    _, receipts, failures = process_directory(receipt_dir, vision, ocr_extractor=ocr)

    assert receipts[0].method == "vision_llm"
    assert failures == []
    assert ocr.calls == []


def test_pipeline_uses_ocr_after_missing_vision_result(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    image_path = receipt_dir / "fallback.jpg"
    image_path.write_bytes(b"fake")
    vision = StubVisionExtractor({"fallback.jpg": None})
    ocr = StubOCRExtractor(
        (
            OCRLine("DATE 06/01/2026", 0.92),
            OCRLine("TOTAL $12.34", 0.88),
        )
    )

    daily_totals, receipts, failures = process_directory(receipt_dir, vision, ocr_extractor=ocr)

    assert daily_totals == {"2026-06-01": 12.34}
    assert receipts[0].method == "ocr_fallback"
    assert receipts[0].confidence == 0.88
    assert failures == []
    assert ocr.calls == [image_path]


def test_pipeline_uses_ocr_after_invalid_vision_result(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "fallback.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor({"fallback.jpg": VisionExtraction(date="bad", total=12.34)})
    ocr = StubOCRExtractor(
        (
            OCRLine("DATE 06/01/2026", 0.92),
            OCRLine("TOTAL $12.34", 0.88),
        )
    )

    _, receipts, failures = process_directory(receipt_dir, vision, ocr_extractor=ocr)

    assert receipts[0].method == "ocr_fallback"
    assert failures == []


def test_pipeline_reports_vision_and_ocr_failure_reasons(tmp_path) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "bad.jpg").write_bytes(b"fake")
    vision = StubVisionExtractor({"bad.jpg": None})
    ocr = StubOCRExtractor((OCRLine("unusable", 0.5),))

    _, receipts, failures = process_directory(receipt_dir, vision, ocr_extractor=ocr)

    assert receipts == []
    assert failures[0].reason == (
        "Vision model did not return valid JSON; OCR candidates were ambiguous: no valid date"
    )
