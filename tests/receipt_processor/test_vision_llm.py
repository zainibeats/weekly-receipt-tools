from io import BytesIO
from pathlib import Path
from typing import Any
import urllib.error

import pytest

from receipt_processor import vision_llm
from receipt_processor.vision_llm import EncodedImage, OllamaVisionExtractor, VisionBackendError


class FakePillowImage:
    def __enter__(self) -> "FakePillowImage":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def convert(self, mode: str) -> "FakePillowImage":
        assert mode == "RGB"
        return self

    def thumbnail(self, size: tuple[int, int]) -> None:
        assert size == (768, 768)

    def save(self, buffer: object, *, format: str, quality: int, optimize: bool) -> None:
        assert format == "JPEG"
        assert quality == 85
        assert optimize is True
        buffer.write(b"jpeg")


def test_ollama_accepts_server_root_url(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float, api_key: str | None = None) -> dict[str, Any]:
        seen["url"] = url
        seen["payload"] = payload
        seen["timeout_seconds"] = timeout_seconds
        seen["api_key"] = api_key
        return {"response": '{"date": "2026-06-01", "total": 12.34}'}

    monkeypatch.setattr(vision_llm, "encode_image", lambda image_path, *, max_edge: EncodedImage("abc", "image/jpeg"))
    monkeypatch.setattr(vision_llm, "post_json", fake_post_json)

    extractor = OllamaVisionExtractor("http://172.23.51.106:11434", "qwen3-vl:8b")
    result = extractor.extract(Path("receipt.jpg"))

    assert seen["url"] == "http://172.23.51.106:11434/api/generate"
    assert seen["payload"]["model"] == "qwen3-vl:8b"
    assert seen["payload"]["images"] == ["abc"]
    assert seen["api_key"] is None
    assert result is not None
    assert result.date == "2026-06-01"
    assert result.total == 12.34


def test_ollama_accepts_generate_endpoint_url(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    def fake_post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float, api_key: str | None = None) -> dict[str, Any]:
        seen["url"] = url
        return {"response": '{"date": "2026-06-01", "total": 12.34}'}

    monkeypatch.setattr(vision_llm, "encode_image", lambda image_path, *, max_edge: EncodedImage("abc", "image/jpeg"))
    monkeypatch.setattr(vision_llm, "post_json", fake_post_json)

    extractor = OllamaVisionExtractor("http://172.23.51.106:11434/api/generate", "qwen3-vl:8b")
    result = extractor.extract(Path("receipt.jpg"))

    assert seen["url"] == "http://172.23.51.106:11434/api/generate"
    assert result is not None


def test_ollama_parses_thinking_field_when_response_is_empty(monkeypatch) -> None:
    def fake_post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float, api_key: str | None = None) -> dict[str, Any]:
        return {
            "response": "",
            "thinking": '{"date": "2026-06-01", "total": 12.34, "currency": "USD", "merchant": "Store"}',
        }

    monkeypatch.setattr(vision_llm, "encode_image", lambda image_path, *, max_edge: EncodedImage("abc", "image/jpeg"))
    monkeypatch.setattr(vision_llm, "post_json", fake_post_json)

    extractor = OllamaVisionExtractor("http://172.23.51.106:11434", "qwen3-vl:8b")
    result = extractor.extract(Path("receipt.jpg"))

    assert result is not None
    assert result.date == "2026-06-01"
    assert result.total == 12.34
    assert result.currency == "USD"
    assert result.merchant == "Store"


def test_post_json_preserves_provider_http_error(monkeypatch) -> None:
    error = urllib.error.HTTPError(
        "http://localhost:11434/api/generate",
        400,
        "Bad Request",
        {},
        BytesIO(b'{"error":"model does not support images"}'),
    )
    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", lambda request, timeout: (_ for _ in ()).throw(error))

    with pytest.raises(VisionBackendError, match=r"HTTP 400: model does not support images"):
        vision_llm.post_json(
            "http://localhost:11434/api/generate",
            {"model": "text-only"},
            timeout_seconds=10,
        )


def test_encode_image_registers_heif_support_for_heic(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(vision_llm, "_register_heif_opener", lambda: calls.append("registered"))
    monkeypatch.setattr(vision_llm.ImageOps, "exif_transpose", lambda image: image)
    monkeypatch.setattr(vision_llm.Image, "open", lambda image_path: FakePillowImage())

    encoded = vision_llm.encode_image(Path("receipt.HEIC"), max_edge=768)

    assert calls == ["registered"]
    assert encoded == EncodedImage("anBlZw==", "image/jpeg")


def test_encode_image_does_not_register_heif_support_for_jpeg(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(vision_llm, "_register_heif_opener", lambda: calls.append("registered"))
    monkeypatch.setattr(vision_llm.ImageOps, "exif_transpose", lambda image: image)
    monkeypatch.setattr(vision_llm.Image, "open", lambda image_path: FakePillowImage())

    encoded = vision_llm.encode_image(Path("receipt.jpg"), max_edge=768)

    assert calls == []
    assert encoded == EncodedImage("anBlZw==", "image/jpeg")


def test_post_json_reports_a_slow_response_as_a_timeout(monkeypatch) -> None:
    class SlowResponse:
        def __enter__(self) -> "SlowResponse":
            return self

        def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
            return None

        def read(self) -> bytes:
            monkeypatch.setattr(vision_llm.time, "monotonic", lambda: 100.0)
            return b'{"response": "{}"}'

    monkeypatch.setattr(vision_llm.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(vision_llm.urllib.request, "urlopen", lambda request, timeout: SlowResponse())

    with pytest.raises(VisionBackendError, match=r"exceeded the 10 second timeout"):
        vision_llm.post_json("http://localhost:11434/api/generate", {}, timeout_seconds=10)


def test_parse_vision_json_reads_the_first_object_from_wrapped_output() -> None:
    result = vision_llm.parse_vision_json(
        'Here is the receipt:\n```json\n{"date": "2026-06-01", "total": 12.34}\n```\n'
        'and a second guess {"date": "2026-06-02", "total": 99.99}'
    )

    assert result is not None
    assert result.date == "2026-06-01"
    assert result.total == 12.34


def test_parse_vision_json_rejects_output_without_an_object() -> None:
    assert vision_llm.parse_vision_json("no JSON here") is None
    assert vision_llm.parse_vision_json('{"date": "2026-06-01"}') is None
