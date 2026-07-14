import json

from receipt_processor import cli
from receipt_processor.ocr import OCRLine
from receipt_processor.vision_llm import VisionExtraction


class StubVisionExtractor:
    def extract(self, image_path):
        return VisionExtraction(date="2026-06-01", total=12.34, confidence=0.82)


def test_cli_defaults_to_edge_vision_pipeline(tmp_path, monkeypatch) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "receipt.jpg").write_bytes(b"fake")
    output_path = tmp_path / "daily.json"
    details_path = tmp_path / "details.json"

    monkeypatch.setattr(cli, "_build_vision_extractor", lambda args: StubVisionExtractor())
    monkeypatch.setattr(
        "sys.argv",
        [
            "receipt-process",
            str(receipt_dir),
            "--output",
            str(output_path),
            "--details",
            str(details_path),
        ],
    )

    cli.main()

    assert json.loads(output_path.read_text(encoding="utf-8")) == {"2026-06-01": 12.34}
    details = json.loads(details_path.read_text(encoding="utf-8"))
    assert details["receipts"][0]["method"] == "vision_llm"


def test_cli_writes_default_outputs_and_prints_summary(tmp_path, monkeypatch, capsys) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "receipt.jpg").write_bytes(b"fake")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_build_vision_extractor", lambda args: StubVisionExtractor())
    monkeypatch.setattr("sys.argv", ["receipt-process", str(receipt_dir)])

    cli.main()

    assert json.loads((tmp_path / "daily_totals.json").read_text(encoding="utf-8")) == {"2026-06-01": 12.34}
    details = json.loads((tmp_path / "receipt_results.json").read_text(encoding="utf-8"))
    assert details["receipts"][0]["file"] == str(receipt_dir / "receipt.jpg")
    summary = capsys.readouterr().out
    assert "Daily totals:" in summary
    assert "Monday 2026-06-01" in summary
    assert "Weekly total: $12.34" in summary
    assert "Wrote daily totals: daily_totals.json" in summary
    assert "Wrote receipt details: receipt_results.json" in summary


def test_cli_enables_ocr_fallback(tmp_path, monkeypatch) -> None:
    receipt_dir = tmp_path / "receipts"
    receipt_dir.mkdir()
    (receipt_dir / "receipt.jpg").write_bytes(b"fake")
    output_path = tmp_path / "daily.json"
    details_path = tmp_path / "details.json"

    class MissingVisionExtractor:
        def extract(self, image_path):
            return None

    class StubOCRExtractor:
        def extract(self, image_path):
            return (
                (
                    OCRLine("DATE 06/01/2026", 0.9),
                    OCRLine("TOTAL 12.34", 0.8),
                ),
            )

    monkeypatch.setattr(cli, "_build_vision_extractor", lambda args: MissingVisionExtractor())
    monkeypatch.setattr(cli, "RapidOCRExtractor", StubOCRExtractor)
    monkeypatch.setattr(
        "sys.argv",
        [
            "receipt-process",
            str(receipt_dir),
            "--ocr-fallback",
            "--output",
            str(output_path),
            "--details",
            str(details_path),
        ],
    )

    cli.main()

    details = json.loads(details_path.read_text(encoding="utf-8"))
    assert details["receipts"][0]["method"] == "ocr_fallback"
