from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedReceipt:
    """Receipt data accepted by validation and included in output files."""

    file: str
    date: str
    total: float
    confidence: float
    method: str


@dataclass(frozen=True)
class ProcessingFailure:
    """A receipt image that could not be extracted or validated."""

    file: str
    reason: str

    @classmethod
    def from_path(cls, path: Path, reason: str) -> "ProcessingFailure":
        return cls(file=str(path), reason=reason)
