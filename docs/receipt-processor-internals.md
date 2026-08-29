# Receipt Processor Internals

The receipt processor lives in `src/receipt_processor`. It is intentionally
small: the CLI gathers configuration, the pipeline owns orchestration, and
single-purpose modules handle extraction, validation, aggregation, and output.

## Processing Flow

1. `cli.py` loads `.env`, parses command line options, and builds a
   `VisionExtractor`.
2. `pipeline.py` finds supported image files in deterministic order.
3. `images.py` opens each image as an upright RGB image, registering HEIC/HEIF
   support only when those inputs appear.
4. `vision_llm.py` resizes each image, sends it to the configured backend, and
   parses the model response into normalized fields.
5. `date_inference.py` resolves yearless dates from exact month/day peers or an
   otherwise unambiguous batch year.
6. When enabled, `ocr.py` runs RapidOCR on the original image and two enhanced
   variants after a missing or invalid vision result.
7. `ocr_parser.py` selects one unambiguous US date and final-total candidate.
8. `validation.py` rejects missing dates/totals, invalid ISO dates, out-of-range
   dates, and non-positive or unusually large totals.
9. `models.py` stores accepted receipts and processing failures.
10. `aggregation.py` sums accepted totals by day.
11. `storage.py` writes detailed receipt and failure JSON.
12. `cli.py` writes daily totals JSON and prints a human-readable summary.

## Module Map

- `aggregation.py`: sums accepted receipt totals by ISO date.
- `cli.py`: command line interface, environment loading, provider selection, and
  console summary formatting.
- `config.py`: simple `.env` loading and environment value parsing.
- `date_inference.py`: conservative year inference from other receipt dates.
- `images.py`: shared receipt image loading and HEIC/HEIF registration.
- `models.py`: dataclasses for accepted receipts and failures.
- `ocr.py`: optional lazy RapidOCR integration and image enhancement.
- `ocr_parser.py`: deterministic OCR date and total candidate parsing.
- `pipeline.py`: image discovery and end-to-end receipt processing.
- `storage.py`: JSON detail output.
- `validation.py`: date and total validation rules.
- `vision_llm.py`: local command, Ollama, and OpenAI-compatible vision
  extractors plus shared image encoding and JSON parsing helpers.

## Extension Points

Add a new model backend by implementing the `VisionExtractor` protocol in
`vision_llm.py`, then wiring it into `_build_vision_extractor` in `cli.py`.

Change receipt acceptance rules in `validation.py`. Keep broad validation there
instead of embedding policy in provider code so all extractors behave
consistently.

Change output details in `models.py` and `storage.py`. Keep `pipeline.py`
focused on control flow rather than output formatting.
