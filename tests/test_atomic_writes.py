from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def load_script_module(name: str, filename: str) -> object:
    """Load a script with a hyphenated filename as an importable test module."""

    script_path = Path(__file__).resolve().parents[1] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve module metadata through sys.modules while the script loads.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


append_image_page = load_script_module("append_image_page", "append-image-page.py")
make_image_grid = load_script_module("make_image_grid", "make-image-grid.py")


class BytesWriter:
    """Minimal PDF-writer stand-in for atomic write tests."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def write(self, file_obj: object) -> None:
        file_obj.write(self.data)


class BytesImage:
    """Minimal Pillow-image stand-in for atomic save tests."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def save(self, file_obj: object, image_format: str, **save_kwargs: object) -> None:
        file_obj.write(self.data)


class EmptyPasswordEncryptedReader:
    """Reader stand-in for an owner-restricted PDF opened with an empty password."""

    is_encrypted = True

    def __init__(self) -> None:
        self.decrypt_calls: list[str] = []

    def decrypt(self, password: str) -> int:
        self.decrypt_calls.append(password)
        return 1


class FakePdfReader:
    """Small pypdf reader stand-in with a configurable page count."""

    def __init__(self, page_count: int) -> None:
        self.is_encrypted = False
        self.metadata: dict[str, str] = {}
        self.pages = [object() for _ in range(page_count)]


class FakePdfWriter:
    """Small pypdf writer stand-in that records added pages and metadata."""

    def __init__(self) -> None:
        self.pages: list[object] = []

    def add_page(self, page: object) -> None:
        self.pages.append(page)

    def add_metadata(self, metadata: dict[str, str]) -> None:
        self.metadata = metadata


class AtomicWriteTests(unittest.TestCase):
    def assert_exits_with_error(self, callback: object, expected: str) -> None:
        """Run a failing callback and assert its stderr contains expected text."""

        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            callback()
        self.assertIn(expected, stderr.getvalue())

    def test_pdf_write_without_overwrite_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "output.pdf"
            destination.write_bytes(b"existing")

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                append_image_page.write_pdf_atomically(
                    BytesWriter(b"new"),
                    destination,
                    overwrite=False,
                )

            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertFalse(list(Path(temp_dir).glob(".output.pdf.*")))

    def test_pdf_write_with_overwrite_replaces_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "output.pdf"
            destination.write_bytes(b"existing")

            append_image_page.write_pdf_atomically(
                BytesWriter(b"new"),
                destination,
                overwrite=True,
            )

            self.assertEqual(destination.read_bytes(), b"new")

    def test_image_write_without_overwrite_refuses_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "output.jpg"
            destination.write_bytes(b"existing")

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                make_image_grid.save_image_atomically(
                    BytesImage(b"new"),
                    destination,
                    "JPEG",
                    overwrite=False,
                )

            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertFalse(list(Path(temp_dir).glob(".output.jpg.*")))

    def test_image_write_with_overwrite_replaces_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "output.jpg"
            destination.write_bytes(b"existing")

            make_image_grid.save_image_atomically(
                BytesImage(b"new"),
                destination,
                "JPEG",
                overwrite=True,
            )

            self.assertEqual(destination.read_bytes(), b"new")

    def test_encrypted_pdf_allows_unrestricted_output_by_default(self) -> None:
        reader = EmptyPasswordEncryptedReader()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            append_image_page.decrypt_reader_if_needed(
                reader,
                Path("restricted.pdf"),
                refuse_unrestricted_output=False,
            )

        self.assertEqual(reader.decrypt_calls, [""])
        self.assertIn("will not preserve input encryption", stderr.getvalue())

    def test_encrypted_pdf_strict_mode_refuses_unrestricted_output(self) -> None:
        reader = EmptyPasswordEncryptedReader()

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            append_image_page.decrypt_reader_if_needed(
                reader,
                Path("restricted.pdf"),
                refuse_unrestricted_output=True,
            )

        self.assertEqual(reader.decrypt_calls, [])

    def test_encrypted_pdf_warning_mentions_lost_restrictions(self) -> None:
        reader = EmptyPasswordEncryptedReader()
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            append_image_page.decrypt_reader_if_needed(
                reader,
                Path("restricted.pdf"),
                refuse_unrestricted_output=False,
            )

        self.assertEqual(reader.decrypt_calls, [""])
        self.assertIn("will not preserve input encryption", stderr.getvalue())

    def test_grid_output_path_must_stay_inside_input_folder_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "images"
            input_dir.mkdir()
            output = root / "outside.jpg"

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                make_image_grid.validate_output_path_safety(
                    output,
                    input_dir,
                    allow_risky_output_path=False,
                )

            make_image_grid.validate_output_path_safety(
                output,
                input_dir,
                allow_risky_output_path=True,
            )

    def test_grid_output_path_refuses_multiple_new_parent_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_dir = Path(temp_dir)
            output = input_dir / "new" / "nested" / "grid.jpg"

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                make_image_grid.validate_output_path_safety(
                    output,
                    input_dir,
                    allow_risky_output_path=False,
                )

    def test_grid_output_suffix_requires_jpeg_before_processing(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            make_image_grid.validate_jpeg_output_path(Path("grid.png"))

        make_image_grid.validate_jpeg_output_path(Path("grid.jpeg"))

    def test_append_output_path_allows_image_or_pdf_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image_dir = root / "images"
            pdf_dir = root / "pdfs"
            image_dir.mkdir()
            pdf_dir.mkdir()

            append_image_page.validate_output_path_safety(
                image_dir / "output.pdf",
                [image_dir, pdf_dir],
                allow_risky_output_path=False,
            )
            append_image_page.validate_output_path_safety(
                pdf_dir / "output.pdf",
                [image_dir, pdf_dir],
                allow_risky_output_path=False,
            )

    def test_append_output_path_refuses_reserved_windows_device_names(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "CON.pdf"

            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                append_image_page.validate_output_path_safety(
                    output,
                    [Path(temp_dir)],
                    allow_risky_output_path=False,
                )

    def test_grid_numeric_limits_must_be_positive_or_in_range(self) -> None:
        base_args = SimpleNamespace(
            input_dir=".",
            output="image-grid.jpg",
            quality=90,
            cell_width=1200,
            cell_height=1600,
            gap=24,
            overwrite=False,
            allow_risky_output_path=False,
            optimize=False,
            max_images=24,
            max_image_pixels=80_000_000,
            max_output_pixels=50_000_000,
        )

        cases = [
            ("quality", 0, "--quality must be between 1 and 100"),
            ("cell_width", 0, "--cell-width and --cell-height must be positive"),
            ("cell_height", 0, "--cell-width and --cell-height must be positive"),
            ("gap", -1, "--gap must be zero or greater"),
            ("max_images", 0, "--max-images must be positive"),
            ("max_image_pixels", 0, "--max-image-pixels must be positive"),
            ("max_output_pixels", 0, "--max-output-pixels must be positive"),
        ]

        for field, value, expected in cases:
            with self.subTest(field=field):
                args = SimpleNamespace(**vars(base_args))
                setattr(args, field, value)
                with patch.object(make_image_grid, "parse_args", return_value=args):
                    self.assert_exits_with_error(make_image_grid.main, expected)

    def test_append_numeric_limits_must_be_positive_or_in_range(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            image = root / "image.jpg"
            pdf = root / "input.pdf"
            output = root / "output.pdf"
            image.write_bytes(b"jpg")
            pdf.write_bytes(b"%PDF")

            base_args = SimpleNamespace(
                image=str(image),
                pdf=str(pdf),
                output=str(output),
                dpi=200,
                overwrite=False,
                allow_risky_output_path=False,
                refuse_unrestricted_output=False,
                max_image_pixels=80_000_000,
                max_pdf_pages=10,
                max_pdf_mb=25,
                pdf_timeout=15.0,
            )

            early_cases = [
                ("max_image_pixels", 0, "--max-image-pixels must be positive"),
                ("max_pdf_pages", 0, "--max-pdf-pages must be positive"),
                ("max_pdf_mb", 0, "--max-pdf-mb must be positive"),
                ("pdf_timeout", 0, "--pdf-timeout must be positive"),
            ]
            for field, value, expected in early_cases:
                with self.subTest(field=field):
                    args = SimpleNamespace(**vars(base_args))
                    setattr(args, field, value)
                    with patch.object(append_image_page, "parse_args", return_value=args):
                        self.assert_exits_with_error(append_image_page.main, expected)

            args = SimpleNamespace(**vars(base_args))
            args.dpi = 71
            with patch.object(append_image_page, "parse_args", return_value=args):
                self.assert_exits_with_error(
                    append_image_page.main,
                    "--dpi must be between 72 and 600",
                )

    def test_append_input_and_output_suffixes_are_validated_before_processing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "input.pdf"
            image = root / "image.jpg"
            pdf.write_bytes(b"%PDF")
            image.write_bytes(b"jpg")

            self.assert_exits_with_error(
                lambda: append_image_page.validate_args(
                    root / "input.txt",
                    image,
                    root / "output.pdf",
                    dpi=200,
                    overwrite=False,
                    max_pdf_bytes=1024,
                    allow_risky_output_path=False,
                ),
                "Input PDF must end with .pdf",
            )
            self.assert_exits_with_error(
                lambda: append_image_page.validate_args(
                    pdf,
                    root / "image.png",
                    root / "output.pdf",
                    dpi=200,
                    overwrite=False,
                    max_pdf_bytes=1024,
                    allow_risky_output_path=False,
                ),
                "Image must be one of",
            )
            self.assert_exits_with_error(
                lambda: append_image_page.validate_args(
                    pdf,
                    image,
                    root / "output.txt",
                    dpi=200,
                    overwrite=False,
                    max_pdf_bytes=1024,
                    allow_risky_output_path=False,
                ),
                "Output path must end with .pdf",
            )

    def test_append_refuses_pdf_file_size_above_configured_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pdf = root / "input.pdf"
            image = root / "image.jpg"
            pdf.write_bytes(b"x" * 11)
            image.write_bytes(b"jpg")

            self.assert_exits_with_error(
                lambda: append_image_page.validate_args(
                    pdf,
                    image,
                    root / "output.pdf",
                    dpi=200,
                    overwrite=False,
                    max_pdf_bytes=10,
                    allow_risky_output_path=False,
                ),
                "above --max-pdf-mb",
            )

    def test_append_refuses_pdf_page_count_above_configured_limit(self) -> None:
        readers = iter([FakePdfReader(3), FakePdfReader(1)])

        def fake_reader(_path: str) -> FakePdfReader:
            return next(readers)

        with (
            patch.object(
                append_image_page,
                "load_pypdf",
                return_value=(fake_reader, FakePdfWriter),
            ),
            patch.object(append_image_page, "write_pdf_atomically") as write_pdf,
        ):
            self.assert_exits_with_error(
                lambda: append_image_page.append_pdf_page_unbounded(
                    Path("input.pdf"),
                    Path("image-page.pdf"),
                    Path("output.pdf"),
                    max_pdf_pages=2,
                    overwrite=False,
                    refuse_unrestricted_output=False,
                ),
                "above --max-pdf-pages",
            )

        write_pdf.assert_not_called()

    def test_append_times_out_stalled_pdf_worker(self) -> None:
        def slow_append(*_args: object) -> int:
            time.sleep(1)
            return 1

        with patch.object(append_image_page, "append_pdf_page_unbounded", slow_append):
            self.assert_exits_with_error(
                lambda: append_image_page.append_pdf_page(
                    Path("input.pdf"),
                    Path("image-page.pdf"),
                    Path("output.pdf"),
                    max_pdf_pages=10,
                    overwrite=False,
                    refuse_unrestricted_output=False,
                    pdf_timeout_seconds=0.01,
                ),
                "exceeded --pdf-timeout",
            )

    def test_grid_refuses_output_pixel_count_above_configured_limit(self) -> None:
        class FakeImageModule:
            @staticmethod
            def new(_mode: str, _size: tuple[int, int], _color: str) -> object:
                return object()

        with (
            patch.object(
                make_image_grid,
                "load_pillow",
                return_value=(FakeImageModule, object(), Exception),
            ),
            patch.object(
                make_image_grid,
                "preflight_images",
                return_value=([Path("one.jpg")], []),
            ),
        ):
            self.assert_exits_with_error(
                lambda: make_image_grid.make_grid(
                    [Path("one.jpg")],
                    Path("grid.jpg"),
                    cell_width=10,
                    cell_height=10,
                    gap=1,
                    quality=90,
                    optimize=False,
                    max_image_pixels=10_000,
                    max_output_pixels=100,
                    overwrite=False,
                ),
                "above --max-output-pixels",
            )


if __name__ == "__main__":
    unittest.main()
