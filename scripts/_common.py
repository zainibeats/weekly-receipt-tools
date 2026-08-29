"""Shared helpers for the image and PDF command line scripts."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO, NoReturn

DEFAULT_MAX_IMAGE_PIXELS = 80_000_000
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def fail(message: str) -> NoReturn:
    """Print an error message and exit with status code 1."""

    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ModuleNotFoundError:
    fail("Missing dependency Pillow. Run: python -m pip install -r requirements.txt")


def first_missing_parent(path: Path) -> Path | None:
    """Return the highest missing directory needed for path, if any."""

    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    return missing[-1] if missing else None


def has_windows_reserved_name(path: Path) -> bool:
    """Return whether any path component is a reserved Windows device name."""

    for part in path.parts:
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            return True
    return False


def validate_output_path_safety(
    output_path: Path,
    allowed_roots: Sequence[Path],
    allow_risky_output_path: bool,
) -> None:
    """Refuse output paths that are easy to mistype into risky locations."""

    if allow_risky_output_path:
        return

    if has_windows_reserved_name(output_path):
        fail(
            "Output path contains a Windows reserved device name. Choose a different "
            "filename or pass --allow-risky-output-path if this is intentional."
        )

    if not any(output_path.is_relative_to(root) for root in allowed_roots):
        roots = ", ".join(str(root) for root in allowed_roots)
        fail(
            f"Output path must be inside {roots}. "
            "Pass --allow-risky-output-path if this destination is intentional."
        )

    missing_parent = first_missing_parent(output_path)
    if missing_parent is not None and missing_parent != output_path.parent:
        fail(
            f"Output path would create multiple missing folders starting at {missing_parent}. "
            "Create the folders first or pass --allow-risky-output-path if this is intentional."
        )


def draft_image_for_size(image: Image.Image, width: int, height: int) -> None:
    """Ask Pillow to decode large JPEGs near the size that will be used."""

    draft = getattr(image, "draft", None)
    if callable(draft):
        draft("RGB", (width, height))


def enforce_image_pixel_limit(
    image: Image.Image,
    image_path: Path,
    max_image_pixels: int,
) -> None:
    """Exit if an image is larger than the configured pixel limit."""

    image_pixels = image.width * image.height
    if image_pixels > max_image_pixels:
        fail(
            f"{image_path} is {image_pixels:,} pixels, above --max-image-pixels "
            f"({max_image_pixels:,}). Resize it or raise the limit."
        )


def fsync_directory(directory: Path) -> None:
    """Best-effort fsync for directory entry changes on platforms that allow it."""

    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return

    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def publish_temp_file(temp_path: Path, destination: Path, overwrite: bool) -> None:
    """Move a completed temporary file into place without racing overwrite checks."""

    if overwrite:
        temp_path.replace(destination)
        return

    # A hard link creates the destination only if it does not already exist.
    try:
        os.link(temp_path, destination)
    except FileExistsError:
        raise FileExistsError(
            f"Output already exists. Pass --overwrite to replace it: {destination}"
        ) from None
    except OSError as exc:
        raise OSError(
            f"Could not create output without overwrite risk: {destination} ({exc})"
        ) from exc
    else:
        temp_path.unlink(missing_ok=True)


def write_atomically(
    destination: Path,
    overwrite: bool,
    write_payload: Callable[[IO[bytes]], None],
) -> None:
    """Write a file durably through a temporary file before replacing the destination."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    fsync_directory(destination.parent)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=destination.suffix or ".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            write_payload(temp_file)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        fsync_directory(destination.parent)
        publish_temp_file(temp_path, destination, overwrite)
        fsync_directory(destination.parent)
    except Exception as exc:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
            fsync_directory(destination.parent)
        fail(f"Could not write {destination}: {exc}")
