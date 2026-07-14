# Receipt OCR Fallback

## Status

Implemented as an opt-in fallback. Broader receipt benchmarking is still
needed before enabling it by default.

## Goal

Recover the transaction date and final charged total from receipts that the
primary vision model cannot extract, especially receipts with faded thermal or
dot-matrix printing and unusual layouts.

The processor must remain practical on consumer-grade Windows hardware:

- 4-6 CPU cores
- 16 GB RAM
- laptop GPU or no GPU
- local processing by default
- dependencies installable with `pip` in a virtual environment or with `uv`

The current minimum vision model remains `mistralai/ministral-3-3b`.

## Current Behavior

The processor resizes each image to a maximum edge of 768 pixels, converts it
to JPEG, and sends it to the configured vision model. Missing fields, malformed
JSON, and values that fail validation are recorded as failures. There is no
image-enhancement retry or OCR fallback.

## Sample Benchmark

Test image: `test-images/ocr-test.png`

The loaded `mistralai/ministral-3-3b` model was tested through LM Studio with
the existing extraction prompt.

| Input | Model result | Prompt tokens |
| --- | --- | ---: |
| 768-pixel maximum edge | `date=null`, `total=null`, confidence `0.75` | 1,121 |
| Original 1360-pixel edge | `date=null`, `total=76.76`, confidence `0.95` | 1,657 |
| RapidOCR, original + 2 variants | `date=2026-07-09`, `total=23.76` | N/A |

The receipt appears to show a total of `023.76` and a date of `07/09`. The
original-resolution retry used about 45% more prompt tokens and returned a
confident but incorrect total. Model confidence must therefore not be treated
as sufficient evidence for accepting a fallback result.

RapidOCR 3.9.1 recognized the key source line as
`033648 12:55 023.76 07/09 18:24 00` on all three variants. The deterministic
parser selected the one date and one monetary candidate. This single success
is an integration check, not an accuracy benchmark.

## Processing Flow

1. Run the existing vision extraction at the configured image size.
2. If the result is missing or fails validation, run OCR against the original
   image and a small number of enhanced variants.
3. Extract date and monetary candidates from the OCR text using deterministic
   parsing rules.
4. Accept a result only when the parser identifies one valid date and one
   sufficiently unambiguous final-total candidate.
5. Otherwise preserve the image as a processing failure for review.

An original-resolution vision retry should not be enabled by default. The
sample benchmark shows that it costs more and can increase confident errors.

## OCR Engine

The implementation uses RapidOCR with the CPU ONNX Runtime backend. It is
pip-installable, works without a GPU, and avoids requiring users to install a
separate Windows executable.

The OCR engine should be initialized lazily so successful vision extractions do
not pay its startup or memory cost.

It is provided through the optional `ocr` dependency group. Benchmark its
installation size, startup time, peak memory use, and accuracy on a
representative receipt set before considering it as a required dependency.

## Image Preprocessing

OCR should use the original image rather than the lossy 768-pixel JPEG sent to
the vision model. Keep the first implementation small and test only variants
that materially improve accuracy:

- EXIF orientation correction and RGB conversion
- grayscale with contrast normalization
- adaptive thresholding for uneven lighting and faded print
- optional mild sharpening or dilation for broken dot-matrix characters

Avoid generating many variants. Each additional pass adds CPU time and can
create conflicting candidates.

## Parsing Rules

### Date

- Accept complete supported dates when they are valid.
- When a receipt contains only a month and day, use the current local calendar
  year.
- For example, `07/09` processed in 2026 becomes `2026-07-09`.
- Continue applying configured `--min-date` and `--max-date` bounds after year
  completion.
- Reject impossible and ambiguous numeric dates rather than swapping day and
  month silently.

The first implementation assumes the existing US-oriented `MM/DD` format.
Supporting locale-specific date order remains a separate decision.

### Total

- Prefer amounts on lines containing labels such as `total`, `amount`,
  `charged`, or `paid`.
- Exclude values that match dates, times, phone numbers, addresses, ticket
  numbers, and configured out-of-range totals.
- If no label survives OCR, accept an unlabeled currency-formatted amount only
  when it is uniquely plausible.
- Do not accept a candidate solely because the OCR engine or model reports high
  confidence.

## Output and Failure Handling

Accepted fallback receipts should use a distinct method such as `ocr_fallback`
in `receipt_results.json`. Failures should state whether vision failed, OCR
found no usable text, or OCR candidates were ambiguous.

OCR-derived results are accepted automatically when all parsing and existing
validation rules pass.

## Implementation

The implementation has:

1. An OCR module with a small extractor protocol and lazy RapidOCR engine.
2. Separate receipt-specific date and total candidate parsing.
3. Pipeline invocation only after a missing or invalid vision result.
4. `--ocr-fallback` and `RECEIPT_OCR_FALLBACK` controls.
5. `ocr_fallback` extraction records and distinct failure reasons.
6. Optional installation through `pip install -e ".[ocr]"`.

## Verification

Automated tests should cover:

- OCR is not called after a valid vision result.
- OCR is called after missing and invalid vision results.
- `07/09` receives the current year.
- full dates retain their stated year.
- labeled totals are preferred over subtotal, tax, cash, and change values.
- phone numbers, times, addresses, and identifiers are not totals.
- ambiguous candidates remain failures.
- fallback receipts are recorded with `method="ocr_fallback"`.
- the pipeline still works when OCR fallback is disabled.

The sample image is retained as a manual integration fixture, with its OCR line
covered by a parser regression test. A larger anonymized set is needed before
using OCR accuracy as a release gate.

## Open Decisions

- Whether RapidOCR is accurate and small enough after benchmarking on the
  target Windows hardware.
- What minimum benchmark set and accuracy threshold are required before the
  fallback is enabled by default.
