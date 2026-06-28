from __future__ import annotations

import base64
import ipaddress
import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageOps

VISION_PROMPT = """Extract the transaction date and final charged total from this receipt image.
Return strict JSON only.
Use null when a field is not visible.
Do not infer values that are not present on the receipt.
Expected shape: {"date": "YYYY-MM-DD", "total": 12.34, "currency": "USD", "merchant": "store", "confidence": 0.82}
"""

HEIF_EXTENSIONS = {".heic", ".heif"}


@dataclass(frozen=True)
class VisionExtraction:
    """Normalized receipt fields returned by a vision backend."""

    date: str
    total: float
    confidence: float = 0.65
    currency: str | None = None
    merchant: str | None = None


class VisionExtractor(Protocol):
    def extract(self, image_path: Path) -> VisionExtraction | None:
        """Extract receipt fields from an image."""


class VisionBackendError(RuntimeError):
    """An HTTP vision backend rejected a request."""


class CommandVisionExtractor:
    """Run a local command that accepts prompt/image JSON on stdin."""

    def __init__(self, command: list[str], timeout_seconds: float = 60.0, max_image_edge: int = 1024) -> None:
        if not command:
            raise ValueError("Vision LLM command must not be empty.")
        self._command = command
        self._timeout_seconds = timeout_seconds
        self._max_image_edge = max_image_edge

    def extract(self, image_path: Path) -> VisionExtraction | None:
        try:
            image = encode_image(image_path, max_edge=self._max_image_edge)
            payload = {
                "prompt": VISION_PROMPT,
                "image_base64": image.data,
                "image_media_type": image.media_type,
            }
            result = subprocess.run(
                self._command,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return None
        if result.returncode != 0:
            return None
        return parse_vision_json(result.stdout)


class OllamaVisionExtractor:
    """Call Ollama's generate endpoint with one receipt image."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        max_image_edge: int = 1024,
        allow_remote: bool = False,
    ) -> None:
        if not model:
            raise ValueError("Vision LLM model must not be empty.")
        validate_local_base_url(base_url, allow_remote=allow_remote)
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_image_edge = max_image_edge

    def extract(self, image_path: Path) -> VisionExtraction | None:
        try:
            image = encode_image(image_path, max_edge=self._max_image_edge)
            payload = {
                "model": self._model,
                "prompt": VISION_PROMPT,
                "images": [image.data],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0, "num_predict": 200},
            }
            data = post_json(_ollama_generate_url(self._base_url), payload, timeout_seconds=self._timeout_seconds)
        except (OSError, ValueError, urllib.error.URLError):
            return None
        extraction = parse_vision_json(str(data.get("response", "")))
        if extraction is not None:
            return extraction
        return parse_vision_json(str(data.get("thinking", "")))


class OpenAICompatibleVisionExtractor:
    """Call a local OpenAI-compatible chat completions endpoint."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        max_image_edge: int = 1024,
        allow_remote: bool = False,
    ) -> None:
        if not model:
            raise ValueError("Vision LLM model must not be empty.")
        validate_local_base_url(base_url, allow_remote=allow_remote)
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._max_image_edge = max_image_edge

    def extract(self, image_path: Path) -> VisionExtraction | None:
        try:
            image = encode_image(image_path, max_edge=self._max_image_edge)
            payload = {
                "model": self._model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{image.media_type};base64,{image.data}"},
                            },
                        ],
                    }
                ],
                "temperature": 0,
                "max_tokens": 800,
            }
            data = post_json(
                f"{self._base_url}/chat/completions",
                payload,
                timeout_seconds=self._timeout_seconds,
                api_key=self._api_key,
            )
        except (OSError, ValueError, urllib.error.URLError):
            return None

        choices = data.get("choices", [])
        if not choices:
            return None
        message = choices[0].get("message", {})
        return parse_vision_json(str(message.get("content", "")))


@dataclass(frozen=True)
class EncodedImage:
    """Base64 JPEG payload sent to vision providers."""

    data: str
    media_type: str


def encode_image(image_path: Path, *, max_edge: int) -> EncodedImage:
    """Resize and encode an image to keep local model requests small."""
    if max_edge <= 0:
        raise ValueError("max_edge must be greater than zero.")

    if image_path.suffix.lower() in HEIF_EXTENSIONS:
        _register_heif_opener()

    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((max_edge, max_edge))
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)

    return EncodedImage(data=base64.b64encode(buffer.getvalue()).decode("ascii"), media_type="image/jpeg")


@lru_cache(maxsize=1)
def _register_heif_opener() -> None:
    """Register HEIC/HEIF support for Pillow when those inputs are used."""
    try:
        from pillow_heif import register_heif_opener
    except ModuleNotFoundError as exc:
        raise ValueError(
            "HEIC/HEIF images require pillow-heif. Run: python -m pip install -r requirements.txt"
        ) from exc

    register_heif_opener()


def parse_vision_json(value: str) -> VisionExtraction | None:
    """Parse the model response into normalized fields, or None when unusable."""
    try:
        data = json.loads(_extract_json_object(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict) or "date" not in data or "total" not in data:
        return None
    if data["date"] is None or data["total"] is None:
        return None

    try:
        confidence = float(data.get("confidence", 0.65))
        return VisionExtraction(
            date=str(data["date"]),
            total=float(data["total"]),
            confidence=confidence,
            currency=_optional_string(data.get("currency")),
            merchant=_optional_string(data.get("merchant")),
        )
    except (TypeError, ValueError):
        return None


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_seconds: float,
    api_key: str | None = None,
) -> dict[str, Any]:
    """POST JSON and return an object response."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started_at = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = _http_error_detail(exc)
        message = f"Vision LLM request failed with HTTP {exc.code}"
        if detail:
            message = f"{message}: {detail}"
        raise VisionBackendError(message) from exc
    # urlopen's timeout is per socket operation, so keep a simple total-time guard too.
    if time.monotonic() - started_at > timeout_seconds:
        return {}
    data = json.loads(body.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _ollama_generate_url(base_url: str) -> str:
    """Return the Ollama generate endpoint for a server root or endpoint URL."""
    stripped = base_url.rstrip("/")
    if stripped.endswith("/api/generate"):
        return stripped
    return f"{stripped}/api/generate"


def _http_error_detail(error: urllib.error.HTTPError) -> str:
    """Return a useful provider error without exposing response headers."""
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
    if not body:
        return ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body
    if isinstance(data, dict) and data.get("error"):
        return str(data["error"])
    return body


def validate_local_base_url(base_url: str, *, allow_remote: bool = False) -> None:
    """Reject remote endpoints by default so receipt images stay local."""
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Vision LLM base URL must be an HTTP URL with a host.")
    if allow_remote:
        return
    if parsed.hostname == "localhost":
        return
    try:
        # Hostnames other than localhost are rejected unless they parse as private IPs.
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError as exc:
        raise ValueError("Vision LLM base URL must use localhost or an IP address unless remote URLs are allowed.") from exc
    if not (address.is_loopback or address.is_private):
        raise ValueError("Vision LLM base URL must be loopback/private unless remote URLs are allowed.")


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _extract_json_object(value: str) -> str:
    """Allow models to wrap JSON in extra text while still requiring an object."""
    stripped = value.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found.")
    return stripped[start : end + 1]
