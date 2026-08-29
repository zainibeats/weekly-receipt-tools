#!/usr/bin/env python3
"""Combine images into a balanced JPG grid."""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from _common import (
    DEFAULT_MAX_IMAGE_PIXELS,
    Image,
    ImageOps,
    draft_image_for_size,
    enforce_image_pixel_limit,
    fail,
    validate_output_path_safety,
    write_atomically,
)

HEIC_EXTENSIONS = {".heic", ".heif"}
JPEG_EXTENSIONS = {".jpg", ".jpeg"}
PNG_EXTENSIONS = {".png"}
SUPPORTED_EXTENSIONS = HEIC_EXTENSIONS | JPEG_EXTENSIONS | PNG_EXTENSIONS
DEFAULT_MAX_IMAGES = 24
DEFAULT_MAX_OUTPUT_PIXELS = 50_000_000


@dataclass(frozen=True)
class GridSize:
    """Number of rows and columns in the output grid."""

    rows: int
    columns: int


@dataclass(frozen=True)
class SkippedImage:
    """An input image that could not be safely used."""

    path: Path
    reason: str


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""

    parser = argparse.ArgumentParser(
        description="Combine HEIC, JPG, and PNG images into a balanced JPG grid."
    )
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=".",
        help="Folder containing images. Defaults to the current folder.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="image-grid.jpg",
        help="Final JPG grid path. Defaults to image-grid.jpg inside input_dir.",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=90,
        help="JPEG quality for the grid output. Defaults to 90.",
    )
    parser.add_argument(
        "--cell-width",
        type=int,
        default=1200,
        help="Width of each grid cell in pixels. Defaults to 1200.",
    )
    parser.add_argument(
        "--cell-height",
        type=int,
        default=1600,
        help="Height of each grid cell in pixels. Defaults to 1600.",
    )
    parser.add_argument(
        "--gap",
        type=int,
        default=24,
        help="Spacing between images in pixels. Defaults to 24.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output JPG if it already exists.",
    )
    parser.add_argument(
        "--allow-risky-output-path",
        action="store_true",
        help=(
            "Allow output paths outside the input folder, paths that create multiple "
            "missing parent folders, or Windows reserved device names."
        ),
    )
    parser.add_argument(
        "--optimize",
        action="store_true",
        help="Ask Pillow to optimize the output JPEG. This can use more memory.",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=DEFAULT_MAX_IMAGES,
        help=f"Maximum number of input images. Defaults to {DEFAULT_MAX_IMAGES}.",
    )
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        default=DEFAULT_MAX_IMAGE_PIXELS,
        help=(
            "Maximum pixels allowed in any one image. "
            f"Defaults to {DEFAULT_MAX_IMAGE_PIXELS}."
        ),
    )
    parser.add_argument(
        "--max-output-pixels",
        type=int,
        default=DEFAULT_MAX_OUTPUT_PIXELS,
        help=(
            "Maximum pixels allowed in the final grid. "
            f"Defaults to {DEFAULT_MAX_OUTPUT_PIXELS}."
        ),
    )
    return parser.parse_args()


def ensure_heif_support() -> None:
    """Register HEIC/HEIF support for Pillow."""

    try:
        from pillow_heif import register_heif_opener
    except ModuleNotFoundError:
        fail("Missing dependency pillow-heif. Run: python -m pip install -r requirements.txt")

    register_heif_opener()


def normalize_dir(path: str) -> Path:
    """Return an absolute input directory path or exit if it is invalid."""

    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        fail(f"Input directory does not exist: {directory}")
    return directory


def validate_jpeg_output_path(output_path: Path) -> None:
    """Refuse unsupported output suffixes before image processing starts."""

    if output_path.suffix.lower() not in JPEG_EXTENSIONS:
        fail("Output file must end with .jpg or .jpeg")


def collect_images(input_dir: Path) -> list[Path]:
    """Return supported, non-hidden image files from the top-level directory."""

    iterator = input_dir.iterdir()
    return sorted(
        path
        for path in iterator
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not path.name.startswith(".")
    )


def preflight_images(
    images: list[Path],
    max_image_pixels: int,
) -> tuple[list[Path], list[SkippedImage]]:
    """Return images that Pillow can identify without failing the whole job."""

    usable_images: list[Path] = []
    skipped_images: list[SkippedImage] = []

    for image_path in images:
        try:
            with Image.open(image_path) as image:
                enforce_image_pixel_limit(image, image_path, max_image_pixels)
                # verify() checks file structure without decoding the full image now.
                image.verify()
        except Exception as exc:
            skipped_images.append(SkippedImage(image_path, str(exc)))
        else:
            usable_images.append(image_path)

    return usable_images, skipped_images


def print_skipped_images(skipped_images: list[SkippedImage]) -> None:
    """Print a concise report of images that were not included."""

    for skipped in skipped_images:
        print(f"skipped: {skipped.path} ({skipped.reason})", file=sys.stderr)


def save_image_atomically(
    image: Image.Image,
    destination: Path,
    image_format: str,
    overwrite: bool,
    **save_kwargs: object,
) -> None:
    """Save an image durably through a temporary file before replacing the destination."""

    write_atomically(
        destination,
        overwrite,
        lambda temp_file: image.save(temp_file, image_format, **save_kwargs),
    )


def balanced_grid_size(count: int) -> GridSize:
    """Calculate a balanced grid size for the number of images."""

    if count < 1:
        fail("No images available for the grid.")
    rows = math.ceil(math.sqrt(count))
    columns = math.ceil(count / rows)
    return GridSize(rows=rows, columns=columns)


def fit_image(image: Image.Image, cell_width: int, cell_height: int) -> Image.Image:
    """Return an RGB copy of an image fitted within a grid cell."""

    draft_image_for_size(image, cell_width, cell_height)
    image = ImageOps.exif_transpose(image)
    image.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image.copy()


def make_grid(
    images: list[Path],
    output: Path,
    cell_width: int,
    cell_height: int,
    gap: int,
    quality: int,
    optimize: bool,
    max_image_pixels: int,
    max_output_pixels: int,
    overwrite: bool,
) -> None:
    """Create and save a JPG grid from image paths."""

    images, skipped_images = preflight_images(images, max_image_pixels)
    print_skipped_images(skipped_images)
    if not images:
        fail("No readable images available for the grid.")

    size = balanced_grid_size(len(images))
    canvas_width = size.columns * cell_width + (size.columns + 1) * gap
    canvas_height = size.rows * cell_height + (size.rows + 1) * gap
    output_pixels = canvas_width * canvas_height
    if output_pixels > max_output_pixels:
        fail(
            f"Grid would be {output_pixels:,} pixels, above --max-output-pixels "
            f"({max_output_pixels:,}). Use smaller cells or raise the limit."
        )

    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")

    pasted_count = 0
    for image_path in images:
        # Use pasted_count so images skipped during decoding do not leave holes in the grid.
        row = pasted_count // size.columns
        column = pasted_count % size.columns
        try:
            with Image.open(image_path) as image:
                fitted = fit_image(image, cell_width, cell_height)
        except Exception as exc:
            skipped_images.append(SkippedImage(image_path, str(exc)))
            print_skipped_images([skipped_images[-1]])
            continue

        x = gap + column * (cell_width + gap) + (cell_width - fitted.width) // 2
        y = gap + row * (cell_height + gap) + (cell_height - fitted.height) // 2
        canvas.paste(fitted, (x, y))
        pasted_count += 1

    if pasted_count < 1:
        fail("No readable images available for the grid.")

    save_image_atomically(
        canvas,
        output,
        "JPEG",
        overwrite,
        quality=quality,
        optimize=optimize,
    )

    print(f"grid: {pasted_count} image(s), {size.rows}x{size.columns}, {output}")
    if skipped_images:
        print(f"skipped: {len(skipped_images)} image(s)")


def main() -> None:
    """Run the image grid command."""

    args = parse_args()

    if not 1 <= args.quality <= 100:
        fail("--quality must be between 1 and 100")
    if args.cell_width < 1 or args.cell_height < 1:
        fail("--cell-width and --cell-height must be positive")
    if args.gap < 0:
        fail("--gap must be zero or greater")
    if args.max_images < 1:
        fail("--max-images must be positive")
    if args.max_image_pixels < 1:
        fail("--max-image-pixels must be positive")
    if args.max_output_pixels < 1:
        fail("--max-output-pixels must be positive")

    Image.MAX_IMAGE_PIXELS = args.max_image_pixels

    input_dir = normalize_dir(args.input_dir)
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = (input_dir / output).resolve()
    validate_jpeg_output_path(output)
    validate_output_path_safety(output, [input_dir], args.allow_risky_output_path)
    if output.exists() and not args.overwrite:
        fail(f"Output already exists. Pass --overwrite to replace it: {output}")

    sources = collect_images(input_dir)
    sources = [path for path in sources if path.resolve() != output]
    if not sources:
        fail(f"No supported images found in {input_dir}")
    if len(sources) > args.max_images:
        fail(
            f"Found {len(sources)} supported image files, above --max-images "
            f"({args.max_images}). Move extras out or raise the limit."
        )
    if any(path.suffix.lower() in HEIC_EXTENSIONS for path in sources):
        ensure_heif_support()

    make_grid(
        sources,
        output,
        args.cell_width,
        args.cell_height,
        args.gap,
        args.quality,
        args.optimize,
        args.max_image_pixels,
        args.max_output_pixels,
        args.overwrite,
    )


if __name__ == "__main__":
    main()
