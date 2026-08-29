#!/usr/bin/env python3
"""Append an image as the last page of a PDF."""

from __future__ import annotations

import argparse
import multiprocessing
import queue
import sys
import tempfile
from pathlib import Path

from _common import (
    DEFAULT_MAX_IMAGE_PIXELS,
    Image,
    ImageOps,
    UnidentifiedImageError,
    draft_image_for_size,
    enforce_image_pixel_limit,
    fail,
    validate_output_path_safety,
    write_atomically,
)

LETTER_SIZE_INCHES = (8.5, 11.0)
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
DEFAULT_MAX_PDF_PAGES = 10
DEFAULT_MAX_PDF_BYTES = 25 * 1024 * 1024
DEFAULT_PDF_TIMEOUT_SECONDS = 15.0


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""

    parser = argparse.ArgumentParser(
        description="Append an image as a US Letter page at the end of a PDF."
    )
    parser.add_argument("image", help="Image to append as the last page.")
    parser.add_argument(
        "--pdf",
        required=True,
        help="PDF to append to.",
    )
    parser.add_argument(
        "-o",
        "--output",
        help=(
            "Final PDF path. Defaults to the image filename with a .pdf extension "
            "in the same folder as the image."
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Raster resolution for the appended image page. Defaults to 200.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the output PDF if it already exists.",
    )
    parser.add_argument(
        "--allow-risky-output-path",
        action="store_true",
        help=(
            "Allow output paths outside the image/PDF folders, paths that create "
            "multiple missing parent folders, or Windows reserved device names."
        ),
    )
    parser.add_argument(
        "--refuse-unrestricted-output",
        action="store_true",
        help=(
            "Refuse owner-restricted PDFs instead of rewriting them as unrestricted "
            "output."
        ),
    )
    parser.add_argument(
        "--max-image-pixels",
        type=int,
        default=DEFAULT_MAX_IMAGE_PIXELS,
        help=(
            "Maximum pixels allowed in the appended image. "
            f"Defaults to {DEFAULT_MAX_IMAGE_PIXELS}."
        ),
    )
    parser.add_argument(
        "--max-pdf-pages",
        type=int,
        default=DEFAULT_MAX_PDF_PAGES,
        help=f"Maximum pages allowed in the input PDF. Defaults to {DEFAULT_MAX_PDF_PAGES}.",
    )
    parser.add_argument(
        "--max-pdf-mb",
        type=int,
        default=DEFAULT_MAX_PDF_BYTES // (1024 * 1024),
        help=(
            "Maximum input PDF file size in MiB before parsing. "
            f"Defaults to {DEFAULT_MAX_PDF_BYTES // (1024 * 1024)}."
        ),
    )
    parser.add_argument(
        "--pdf-timeout",
        type=float,
        default=DEFAULT_PDF_TIMEOUT_SECONDS,
        help=(
            "Maximum seconds allowed for PDF parsing and rewriting before the "
            f"worker process is stopped. Defaults to {DEFAULT_PDF_TIMEOUT_SECONDS:g}."
        ),
    )
    return parser.parse_args()


def load_pypdf() -> tuple[object, object]:
    """Load pypdf objects or exit with an install hint."""

    try:
        from pypdf import PdfReader, PdfWriter
    except ModuleNotFoundError:
        fail("Missing dependency pypdf. Run: python -m pip install -r requirements.txt")
    return PdfReader, PdfWriter


def existing_file(path: str, label: str) -> Path:
    """Return an existing file path or exit with a labeled error."""

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        fail(f"{label} does not exist: {resolved}")
    return resolved


def default_output_path(image_path: Path) -> Path:
    """Return the default output PDF path for an image."""

    return image_path.with_suffix(".pdf")


def resolve_output_path(output: str | None, image_path: Path) -> Path:
    """Resolve the requested output path or use the default."""

    if output is None:
        return default_output_path(image_path)
    return Path(output).expanduser().resolve()


def validate_args(
    pdf_path: Path,
    image_path: Path,
    output_path: Path,
    dpi: int,
    overwrite: bool,
    max_pdf_bytes: int,
    allow_risky_output_path: bool,
) -> None:
    """Validate paths and command line option ranges."""

    if pdf_path.suffix.lower() != ".pdf":
        fail(f"Input PDF must end with .pdf: {pdf_path}")
    pdf_size = pdf_path.stat().st_size
    if pdf_size > max_pdf_bytes:
        fail(
            f"Input PDF is {pdf_size:,} bytes, above --max-pdf-mb "
            f"({max_pdf_bytes // (1024 * 1024)} MiB). Use a smaller PDF or raise the limit."
        )
    if image_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        fail(
            "Image must be one of: "
            + ", ".join(sorted(SUPPORTED_IMAGE_EXTENSIONS))
            + f" ({image_path})"
        )
    if output_path.suffix.lower() != ".pdf":
        fail(f"Output path must end with .pdf: {output_path}")
    if output_path == pdf_path:
        fail("Output path cannot be the same as the input PDF.")
    validate_output_path_safety(
        output_path,
        [image_path.parent, pdf_path.parent],
        allow_risky_output_path,
    )
    if output_path.exists() and not overwrite:
        fail(f"Output already exists. Pass --overwrite to replace it: {output_path}")
    if dpi < 72 or dpi > 600:
        fail("--dpi must be between 72 and 600")


def make_contained_image_pdf(
    image_path: Path,
    pdf_path: Path,
    dpi: int,
    max_image_pixels: int,
) -> None:
    """Create a one-page PDF with the image centered on a letter-size page."""

    width_inches, height_inches = LETTER_SIZE_INCHES
    page_width = round(width_inches * dpi)
    page_height = round(height_inches * dpi)

    try:
        with Image.open(image_path) as image:
            enforce_image_pixel_limit(image, image_path, max_image_pixels)
            draft_image_for_size(image, page_width, page_height)
            image = ImageOps.exif_transpose(image)
            image.thumbnail((page_width, page_height), Image.Resampling.LANCZOS)
            if image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                # PDF output has no alpha channel here, so flatten transparent pixels to white.
                background = Image.new("RGB", image.size, "white")
                alpha = image.convert("RGBA").split()[-1]
                background.paste(image.convert("RGB"), mask=alpha)
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            page = Image.new("RGB", (page_width, page_height), "white")
            x = (page_width - image.width) // 2
            y = (page_height - image.height) // 2
            page.paste(image, (x, y))
            page.save(pdf_path, "PDF", resolution=dpi)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        fail(f"Could not read image {image_path}: {exc}")


def write_pdf_atomically(writer: object, output_pdf: Path, overwrite: bool) -> None:
    """Save a PDF durably through a temporary file before replacing the destination."""

    write_atomically(output_pdf, overwrite, writer.write)  # type: ignore[attr-defined]


def decrypt_reader_if_needed(
    reader: object,
    source_pdf: Path,
    refuse_unrestricted_output: bool,
) -> None:
    """Try to open an encrypted PDF that does not require a user password."""

    if not getattr(reader, "is_encrypted", False):
        return

    if refuse_unrestricted_output:
        fail(
            "Input PDF is encrypted or owner-restricted. This script cannot preserve "
            "PDF encryption or permission restrictions in the output."
        )

    # Empty-password decryption handles owner-restricted PDFs, but not user-locked PDFs.
    try:
        decrypt_result = reader.decrypt("")  # type: ignore[attr-defined]
    except Exception as exc:
        fail(f"Could not decrypt input PDF {source_pdf} with an empty password: {exc}")

    if not decrypt_result:
        fail(
            "Input PDF is encrypted and could not be opened with an empty password. "
            "This script can handle owner-restricted PDFs, but not PDFs that require a password."
        )

    print(
        "WARNING: output PDF will not preserve input encryption or permission restrictions.",
        file=sys.stderr,
    )


def append_pdf_page(
    source_pdf: Path,
    image_page_pdf: Path,
    output_pdf: Path,
    max_pdf_pages: int,
    overwrite: bool,
    refuse_unrestricted_output: bool,
    pdf_timeout_seconds: float,
) -> int:
    """Append the generated image page PDF under a subprocess timeout boundary."""

    # Keep pypdf parsing in a child process so malformed PDFs cannot stall the CLI forever.
    result_queue: multiprocessing.Queue = multiprocessing.Queue(maxsize=1)
    process = multiprocessing.Process(
        target=append_pdf_page_worker,
        args=(
            source_pdf,
            image_page_pdf,
            output_pdf,
            max_pdf_pages,
            overwrite,
            refuse_unrestricted_output,
            result_queue,
        ),
    )
    process.start()
    process.join(pdf_timeout_seconds)

    if process.is_alive():
        process.terminate()
        process.join()
        fail(
            f"PDF parsing exceeded --pdf-timeout ({pdf_timeout_seconds:g} seconds). "
            "The input PDF may be malformed or too complex."
        )

    try:
        status, payload = result_queue.get_nowait()
    except queue.Empty:
        if process.exitcode == 0:
            fail("PDF worker finished without returning a result.")
        fail(f"PDF worker exited unexpectedly with status {process.exitcode}.")

    if status == "ok":
        return int(payload)
    if status == "system_exit":
        raise SystemExit(int(payload))
    fail(str(payload))


def append_pdf_page_worker(
    source_pdf: Path,
    image_page_pdf: Path,
    output_pdf: Path,
    max_pdf_pages: int,
    overwrite: bool,
    refuse_unrestricted_output: bool,
    result_queue: multiprocessing.Queue,
) -> None:
    """Run PDF parsing and rewriting in a child process."""

    try:
        page_count = append_pdf_page_unbounded(
            source_pdf,
            image_page_pdf,
            output_pdf,
            max_pdf_pages,
            overwrite,
            refuse_unrestricted_output,
        )
    except SystemExit as exc:
        # Preserve deliberate fail() exits from the worker as normal CLI exits.
        result_queue.put(("system_exit", exc.code if isinstance(exc.code, int) else 1))
    except BaseException as exc:
        result_queue.put(("error", f"Could not append PDF page: {exc}"))
    else:
        result_queue.put(("ok", page_count))


def append_pdf_page_unbounded(
    source_pdf: Path,
    image_page_pdf: Path,
    output_pdf: Path,
    max_pdf_pages: int,
    overwrite: bool,
    refuse_unrestricted_output: bool,
) -> int:
    """Append the generated image page PDF to the source PDF."""

    PdfReader, PdfWriter = load_pypdf()

    try:
        source_reader = PdfReader(str(source_pdf))
        image_reader = PdfReader(str(image_page_pdf))
    except Exception as exc:
        fail(f"Could not read PDF input: {exc}")

    decrypt_reader_if_needed(source_reader, source_pdf, refuse_unrestricted_output)

    source_page_count = len(source_reader.pages)
    if source_page_count < 1:
        fail(f"Input PDF has no pages: {source_pdf}")
    if source_page_count > max_pdf_pages:
        fail(
            f"Input PDF has {source_page_count} pages, above --max-pdf-pages "
            f"({max_pdf_pages}). Raise the limit if this is expected."
        )
    if len(image_reader.pages) != 1:
        fail(f"Internal image page PDF should have one page, found {len(image_reader.pages)}.")

    writer = PdfWriter()
    for page in source_reader.pages:
        writer.add_page(page)
    writer.add_page(image_reader.pages[0])

    if source_reader.metadata:
        # pypdf accepts string metadata values; skip richer objects from malformed inputs.
        metadata = {
            key: value
            for key, value in source_reader.metadata.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if metadata:
            writer.add_metadata(metadata)

    write_pdf_atomically(writer, output_pdf, overwrite)

    return len(writer.pages)


def main() -> None:
    """Run the append-image-page command."""

    args = parse_args()
    if args.max_image_pixels < 1:
        fail("--max-image-pixels must be positive")
    if args.max_pdf_pages < 1:
        fail("--max-pdf-pages must be positive")
    if args.max_pdf_mb < 1:
        fail("--max-pdf-mb must be positive")
    if args.pdf_timeout <= 0:
        fail("--pdf-timeout must be positive")

    pdf_path = existing_file(args.pdf, "PDF")
    image_path = existing_file(args.image, "Image")
    output_path = resolve_output_path(args.output, image_path)

    max_pdf_bytes = args.max_pdf_mb * 1024 * 1024
    validate_args(
        pdf_path,
        image_path,
        output_path,
        args.dpi,
        args.overwrite,
        max_pdf_bytes,
        args.allow_risky_output_path,
    )

    Image.MAX_IMAGE_PIXELS = args.max_image_pixels

    with tempfile.TemporaryDirectory() as temp_dir:
        image_page_pdf = Path(temp_dir) / "image-page.pdf"
        make_contained_image_pdf(
            image_path,
            image_page_pdf,
            args.dpi,
            args.max_image_pixels,
        )
        page_count = append_pdf_page(
            pdf_path,
            image_page_pdf,
            output_path,
            args.max_pdf_pages,
            args.overwrite,
            args.refuse_unrestricted_output,
            args.pdf_timeout,
        )

    print(f"wrote: {output_path}")
    print(f"pages: {page_count}")


if __name__ == "__main__":
    main()
