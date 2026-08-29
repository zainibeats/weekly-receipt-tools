from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageOps

HEIF_EXTENSIONS = {".heic", ".heif"}


def load_rgb_image(image_path: Path) -> Image.Image:
    """Open a receipt image as an upright RGB image, including HEIC/HEIF input."""
    if image_path.suffix.lower() in HEIF_EXTENSIONS:
        register_heif_opener()

    with Image.open(image_path) as source:
        return ImageOps.exif_transpose(source).convert("RGB")


@lru_cache(maxsize=1)
def register_heif_opener() -> None:
    """Register HEIC/HEIF support for Pillow when those inputs are used."""
    try:
        from pillow_heif import register_heif_opener as register
    except ModuleNotFoundError as exc:
        raise ValueError(
            "HEIC/HEIF images require pillow-heif. Run: python -m pip install -r requirements.txt"
        ) from exc

    register()
