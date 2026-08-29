from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from receipt_processor.models import ExtractedReceipt, ProcessingFailure


def write_daily_totals_json(path: Path, daily_totals: dict[str, float]) -> None:
    """Write accepted receipt totals summed by ISO date."""
    _write_json(path, daily_totals)


def write_processing_details_json(
    path: Path,
    receipts: list[ExtractedReceipt],
    failures: list[ProcessingFailure],
) -> None:
    """Write accepted receipts and failed receipt paths."""
    _write_json(
        path,
        {
            "receipts": [asdict(receipt) for receipt in receipts],
            "failures": [asdict(failure) for failure in failures],
        },
    )


def _write_json(path: Path, payload: Any) -> None:
    """Write JSON through a temporary file so an interrupted run keeps the old output."""
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        temp_file.flush()
        os.fsync(temp_file.fileno())

    try:
        os.replace(temp_path, path)
    except OSError:
        temp_path.unlink(missing_ok=True)
        raise
