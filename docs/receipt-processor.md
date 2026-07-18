# Receipt Processor

`receipt-process` sends receipt images to a configured local vision model,
extracts transaction dates and totals, and writes daily summary JSON.

## Usage

Process receipt images directly with the configured local vision model:

```bash
receipt-process path/to/receipts
```

Supported input formats include HEIC, HEIF, JPG, JPEG, PNG, TIFF, BMP, and WebP.
Images are resized and sent to the vision model as JPEG payloads.

For best results, use one receipt per image. The current receipt extraction
flow appears to handle only one receipt from each image reliably.

By default this writes:

- `daily_totals.json`
- `receipt_results.json`

Override output paths when needed:

```bash
receipt-process path/to/receipts --output daily_totals.json --details receipt_results.json
```

Constrain accepted receipt dates:

```bash
receipt-process path/to/receipts --min-date 2026-06-01 --max-date 2026-06-30
```

## OCR Fallback

Install the optional local OCR dependencies:

```bash
python -m pip install -e ".[ocr]"
```

Enable OCR only after missing or invalid vision results:

```bash
receipt-process path/to/receipts --ocr-fallback
```

Set `RECEIPT_OCR_FALLBACK=true` to enable it through `.env`. RapidOCR and its
ONNX Runtime engine are loaded lazily, so valid vision results do not pay their
startup or memory cost. OCR runs against the original image and two enhanced
variants.

Date parsing is intentionally conservative. For dates without a year, the
processor first looks for an exact month/day match among fully dated receipts,
then uses the batch year only when it is unambiguous. It never substitutes the
machine's local year. OCR prefers explicitly labeled final totals and leaves
conflicting candidates for manual review.

## Local Model Config

Example `.env` values:

```bash
RECEIPT_VISION_PROVIDER=openai-compatible
RECEIPT_VISION_BASE_URL=http://127.0.0.1:8000/v1
RECEIPT_VISION_MODEL=mistralai/ministral-3-3b
RECEIPT_VISION_TIMEOUT=90
RECEIPT_VISION_MAX_IMAGE_EDGE=768
RECEIPT_OCR_FALLBACK=false
```

Use `RECEIPT_VISION_PROVIDER=ollama` with
`RECEIPT_VISION_BASE_URL=http://127.0.0.1:11434` for Ollama.
The selected Ollama model must list `vision` under `Capabilities` in
`ollama show <model>`. A model imported without its vision projector can handle
text prompts but Ollama will reject receipt images with HTTP 400.

The processor rejects remote HTTP endpoints by default so receipt images stay on
local or private-network model servers. Pass `--vision-llm-allow-remote` only
when sending receipt images to a remote endpoint is intentional.

## Providers

- `openai-compatible`: calls a local OpenAI-compatible chat completions
  endpoint.
- `ollama`: calls Ollama's local generate endpoint.
- `command`: runs a local command and sends JSON with the prompt and base64 JPEG
  image on stdin.

## Outputs

`daily_totals.json` contains accepted receipt totals summed by ISO date.

`receipt_results.json` contains:

- `receipts`: accepted receipt records with file, date, total, confidence, and
  method.
- `failures`: image paths that could not be extracted or failed validation.

See [receipt processor internals](receipt-processor-internals.md) for the code
layout and processing flow.
