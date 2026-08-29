from pathlib import Path

import pytest

from receipt_processor import images


class FakePillowImage:
    def __enter__(self) -> "FakePillowImage":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def convert(self, mode: str) -> "FakePillowImage":
        assert mode == "RGB"
        return self


@pytest.fixture(autouse=True)
def fake_pillow(monkeypatch) -> None:
    monkeypatch.setattr(images.Image, "open", lambda image_path: FakePillowImage())
    monkeypatch.setattr(images.ImageOps, "exif_transpose", lambda image: image)


def test_load_rgb_image_registers_heif_support_for_heic(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(images, "register_heif_opener", lambda: calls.append("registered"))

    images.load_rgb_image(Path("receipt.HEIC"))

    assert calls == ["registered"]


def test_load_rgb_image_does_not_register_heif_support_for_jpeg(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(images, "register_heif_opener", lambda: calls.append("registered"))

    images.load_rgb_image(Path("receipt.jpg"))

    assert calls == []
