from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageFilter, ImageOps

from receipt_processor.images import load_rgb_image


@dataclass(frozen=True)
class OCRLine:
    """One line of OCR text and its recognition confidence."""

    text: str
    confidence: float


OCRVariant = tuple[OCRLine, ...]


class OCRExtractor(Protocol):
    def extract(self, image_path: Path) -> tuple[OCRVariant, ...]:
        """Read text from an original receipt and a few enhanced variants."""


class RapidOCRExtractor:
    """Run RapidOCR locally, loading its engine only on the first fallback."""

    @cached_property
    def _engine(self) -> Any:
        try:
            from rapidocr import RapidOCR
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                'OCR fallback requires optional dependencies. Install with: pip install -e ".[ocr]"'
            ) from exc
        return RapidOCR(params={"Global.log_level": "critical"})

    def extract(self, image_path: Path) -> tuple[OCRVariant, ...]:
        variants = _image_variants(load_rgb_image(image_path))
        return tuple(self._recognize(variant) for variant in variants)

    def _recognize(self, image: Image.Image) -> OCRVariant:
        np = _load_numpy()
        result = self._engine(np.asarray(image))
        texts = getattr(result, "txts", None) or ()
        scores = getattr(result, "scores", None) or ()
        return tuple(
            OCRLine(text=str(text).strip(), confidence=float(score))
            for text, score in zip(texts, scores, strict=False)
            if str(text).strip()
        )


def _image_variants(image: Image.Image) -> tuple[Image.Image, ...]:
    """Return the original plus two inexpensive faded-print enhancements."""
    np = _load_numpy()
    grayscale = ImageOps.autocontrast(ImageOps.grayscale(image))
    sharpened = grayscale.filter(ImageFilter.UnsharpMask(radius=1, percent=130, threshold=2))
    local_mean = grayscale.filter(ImageFilter.BoxBlur(radius=15))
    threshold_data = np.where(np.asarray(grayscale) > np.asarray(local_mean) - 12, 255, 0).astype("uint8")
    threshold = Image.fromarray(threshold_data)
    return image, sharpened, threshold


def _load_numpy() -> Any:
    try:
        import numpy as np
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            'OCR fallback requires optional dependencies. Install with: pip install -e ".[ocr]"'
        ) from exc
    return np
