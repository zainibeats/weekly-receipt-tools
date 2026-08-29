import json

import pytest

from receipt_processor import storage
from receipt_processor.models import ExtractedReceipt, ProcessingFailure
from receipt_processor.storage import write_daily_totals_json, write_processing_details_json


def test_writes_processing_details_json(tmp_path) -> None:
    output_path = tmp_path / "details.json"

    write_processing_details_json(
        output_path,
        [ExtractedReceipt("a.jpg", "2026-06-01", 12.34, 0.9, "vision_llm")],
        [ProcessingFailure("b.jpg", "failed")],
    )

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "failures": [{"file": "b.jpg", "reason": "failed"}],
        "receipts": [
            {
                "confidence": 0.9,
                "date": "2026-06-01",
                "file": "a.jpg",
                "method": "vision_llm",
                "total": 12.34,
            }
        ],
    }


def test_writes_daily_totals_json(tmp_path) -> None:
    path = tmp_path / "daily_totals.json"

    write_daily_totals_json(path, {"2026-06-02": 5.0, "2026-06-01": 12.34})

    assert json.loads(path.read_text(encoding="utf-8")) == {"2026-06-01": 12.34, "2026-06-02": 5.0}
    assert not list(tmp_path.glob(".daily_totals.json.*"))


def test_failed_write_leaves_the_previous_output_in_place(tmp_path, monkeypatch) -> None:
    path = tmp_path / "daily_totals.json"
    write_daily_totals_json(path, {"2026-06-01": 12.34})

    def failing_replace(source: object, destination: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", failing_replace)
    with pytest.raises(OSError):
        write_daily_totals_json(path, {"2026-06-01": 99.99})

    assert json.loads(path.read_text(encoding="utf-8")) == {"2026-06-01": 12.34}
    assert not list(tmp_path.glob(".daily_totals.json.*"))
