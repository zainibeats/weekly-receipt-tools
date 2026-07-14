from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path

from receipt_processor.config import env_bool, env_float, env_int, load_env_file
from receipt_processor.models import ProcessingFailure
from receipt_processor.ocr import RapidOCRExtractor
from receipt_processor.pipeline import process_directory
from receipt_processor.storage import write_processing_details_json
from receipt_processor.vision_llm import (
    CommandVisionExtractor,
    OllamaVisionExtractor,
    OpenAICompatibleVisionExtractor,
    VisionExtractor,
)

DEFAULT_DAILY_TOTALS_PATH = Path("daily_totals.json")
DEFAULT_DETAILS_PATH = Path("receipt_results.json")


def main() -> None:
    """Run the receipt processor command line interface."""
    load_env_file()
    parser = argparse.ArgumentParser(description="Extract receipt totals and aggregate spend by day.")
    parser.add_argument("input_dir", type=Path, help="Directory containing receipt images.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_DAILY_TOTALS_PATH,
        help=f"Write daily totals JSON to this path. Defaults to {DEFAULT_DAILY_TOTALS_PATH}.",
    )
    parser.add_argument(
        "--details",
        type=Path,
        default=DEFAULT_DETAILS_PATH,
        help=f"Write per-receipt results and failures JSON to this path. Defaults to {DEFAULT_DETAILS_PATH}.",
    )
    parser.add_argument("--min-date", type=_parse_cli_date, help="Reject receipts before this date, in YYYY-MM-DD format.")
    parser.add_argument("--max-date", type=_parse_cli_date, help="Reject receipts after this date, in YYYY-MM-DD format.")
    parser.add_argument(
        "--vision-llm-provider",
        choices=["command", "ollama", "openai-compatible"],
        default=os.environ.get("RECEIPT_VISION_PROVIDER", "openai-compatible"),
        help="Local vision LLM provider. Defaults to RECEIPT_VISION_PROVIDER or openai-compatible.",
    )
    parser.add_argument(
        "--vision-llm-command",
        nargs="+",
        help="Command vision extractor. JSON with prompt and base64 JPEG is sent on stdin.",
    )
    parser.add_argument("--vision-llm-base-url", default=os.environ.get("RECEIPT_VISION_BASE_URL"), help="Local vision LLM HTTP base URL.")
    parser.add_argument(
        "--vision-llm-model",
        default=os.environ.get("RECEIPT_VISION_MODEL", "mistralai/ministral-3-3b"),
        help="Local vision LLM model name.",
    )
    parser.add_argument(
        "--vision-llm-timeout",
        type=float,
        default=env_float("RECEIPT_VISION_TIMEOUT", 90.0),
        help="Vision LLM timeout in seconds.",
    )
    parser.add_argument(
        "--vision-llm-max-image-edge",
        type=int,
        default=env_int("RECEIPT_VISION_MAX_IMAGE_EDGE", 768),
        help="Max long edge sent to the vision LLM.",
    )
    parser.add_argument(
        "--vision-llm-allow-remote",
        action="store_true",
        default=env_bool("RECEIPT_VISION_ALLOW_REMOTE"),
        help="Allow non-local vision LLM endpoints. Not recommended for receipt images.",
    )
    parser.add_argument(
        "--ocr-fallback",
        action=argparse.BooleanOptionalAction,
        default=env_bool("RECEIPT_OCR_FALLBACK"),
        help="Use local RapidOCR after a missing or invalid vision result.",
    )
    args = parser.parse_args()
    vision_extractor = _build_vision_extractor(args)
    daily_totals, receipts, failures = process_directory(
        args.input_dir,
        vision_extractor,
        min_date=args.min_date,
        max_date=args.max_date,
        ocr_extractor=RapidOCRExtractor() if args.ocr_fallback else None,
    )

    args.output.write_text(json.dumps(daily_totals, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_processing_details_json(args.details, receipts, failures)
    print(_format_summary(daily_totals, failures, args.output, args.details))


def _parse_cli_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _format_summary(
    daily_totals: dict[str, float],
    failures: list[ProcessingFailure],
    output_path: Path,
    details_path: Path,
) -> str:
    lines = ["Daily totals:"]
    if daily_totals:
        for day, total in sorted(daily_totals.items()):
            lines.append(f"{_format_day_label(day):<16} ${total:,.2f}")
    else:
        lines.append("No receipt totals found.")

    weekly_total = sum(daily_totals.values())
    lines.extend(
        [
            "",
            f"Weekly total: ${weekly_total:,.2f}",
            "",
            f"Wrote daily totals: {output_path}",
            f"Wrote receipt details: {details_path}",
        ]
    )
    if failures:
        lines.extend(["", "Needs review:"])
        for failure in failures:
            lines.append(f"- {failure.file}: {failure.reason}")
    return "\n".join(lines)


def _format_day_label(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return value
    return f"{parsed.strftime('%A')} {value}"


def _build_vision_extractor(args: argparse.Namespace) -> VisionExtractor:
    """Build the configured vision backend from CLI args and environment values."""
    if args.vision_llm_provider == "command":
        if not args.vision_llm_command:
            raise SystemExit("--vision-llm-command is required when --vision-llm-provider=command")
        return CommandVisionExtractor(
            args.vision_llm_command,
            timeout_seconds=args.vision_llm_timeout,
            max_image_edge=args.vision_llm_max_image_edge,
        )

    if not args.vision_llm_model:
        raise SystemExit("--vision-llm-model is required for HTTP vision LLM providers")

    if args.vision_llm_provider == "ollama":
        return OllamaVisionExtractor(
            args.vision_llm_base_url or "http://127.0.0.1:11434",
            args.vision_llm_model,
            timeout_seconds=args.vision_llm_timeout,
            max_image_edge=args.vision_llm_max_image_edge,
            allow_remote=args.vision_llm_allow_remote,
        )

    return OpenAICompatibleVisionExtractor(
        args.vision_llm_base_url or "http://127.0.0.1:8000/v1",
        args.vision_llm_model,
        api_key=os.environ.get("RECEIPT_VISION_API_KEY") or os.environ.get("VISION_LLM_API_KEY"),
        timeout_seconds=args.vision_llm_timeout,
        max_image_edge=args.vision_llm_max_image_edge,
        allow_remote=args.vision_llm_allow_remote,
    )


if __name__ == "__main__":
    main()
