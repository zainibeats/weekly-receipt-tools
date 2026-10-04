# Receipt Processor Redesign Plan

Status: proposal, nothing implemented yet.

## Goal

Stop checking every total by hand. Each receipt ends as either:

- `verified`: two independent reads agree and the arithmetic checks pass, or
- `check`: anything else, shown with the competing values.

A silently wrong total is the failure to avoid. Extra `check` flags are an
acceptable cost.

Constraints: local models on consumer hardware only (currently RTX 5080, LM
Studio, Ministral 3 3B). No cloud services. The two image/PDF scripts are out
of scope.

## Evidence

From the 20 labeled receipts on the `test-images` branch. Labels match the
receipts except IMG_9964, whose true date is 2026-09-11.

| Result today                        | Count | Receipts                       |
| ----------------------------------- | ----: | ------------------------------ |
| Correct                             |    13 |                                |
| Silently wrong date (total correct) |     1 | IMG_9964 (09/11 read as 11/09) |
| Silently wrong total                |     3 | IMG_9978, IMG_9993, IMG_9998   |
| Sent to review                      |     3 | IMG_9960, IMG_9963, IMG_9966   |

All 20 are parking receipts in four layouts:

- FLASH slips (SP Plus / ASLU): 14
- SP+ "PAID RECEIPT" cards with a faded dot-matrix line: 4
- Impark: 1
- Plain "RECEIPT" slip with MM/DD/YY dates: 1

Findings:

1. None of the three wrong totals is printed on its receipt (35.06 vs 17.51,
   7.50 vs 3.75, 5.75 vs 3.75). RapidOCR read the true total on all 16 printed
   receipts, and none of the 3 wrong values appear in its text. Every correct
   model total does appear in it.
2. The model's self-reported confidence was 0.95 to 1.0 on the wrong totals,
   so it carries no signal.
3. On FLASH slips, OCR returns each value on the line before its label
   (`$17.51`, `Total:`), and parking fee + tax = total on every one.
4. The SP+ cards hold everything in one dot-matrix line, for example
   `035031 14:16 020.01 09/10 18:31 00` (amount 020.01, date 09/10, no year).
   The current OCR variants read that line fully on 1 of 4 cards. Shrinking the
   image to a quarter of its size merges the dots, and OCR then read all 4. One
   card needed a blur variant to fix a "2" misread as "Z".
5. `ocr_parser.py` only understands numeric dates. It cannot parse
   "18 Sep 2026" or "Sep 10, 2026", so OCR alone scored 2 of 20.
6. Image resolution is not the problem. These receipts are legible at the
   768px the model receives.
7. When the model returns `null` for a field, the review reason says "did not
   return valid JSON", which hides what actually happened.

## Proposed Pipeline

1. **Load image**: unchanged.
2. **OCR every receipt** with RapidOCR on the CPU. Keep the three current
   variants and add a quarter-scale variant and a blurred variant for
   dot-matrix print.
3. **Parse the OCR text.**
   - Date candidates also accept month-name formats.
   - Small parsers for the known layouts, then the existing generic logic:
     - FLASH slip: the value line before `Total:`, which must equal fee + tax.
     - SP+ card: a fixed regex for the stamp line; the year comes from the
       rest of the batch, as today.
     - Impark: `Total Paid:` and the `SEP 10, 2026` date.
4. **Vision model**: run separately, never shown the OCR result. Ministral 3
   3B stays the default. The prompt and JSON schema ask for parking fee, tax
   and total, plus the date. LM Studio supports JSON schema output.
5. **Reconcile.**
   - `verified` needs all of:
     - the model and OCR give the same total and the same date
     - the arithmetic holds where the layout has it
     - the date passes the bounds, including "not in the future"
   - Everything else is `check`, recorded with both reads.
6. **Output.**
   - `receipt_results.json` gains each receipt's status, the OCR total and
     date, the model total and date, the OCR text and the raw model reply.
   - The terminal summary marks each receipt `verified` or `check`.

Example summary:

```
Daily totals (verified receipts only):
Thursday 2026-09-10   $12.00
...

Check these (2):
- IMG_9993: model $7.50, OCR $3.75 (Total line). Not added.
- IMG_9963: OCR $16.26 on 2026-09-11, model gave no answer. Not added.
```

7. **Eval command**: `receipt-eval IMAGE_DIR LABELS.json`. It reports per
   receipt and in total:
   - verified and right
   - verified but wrong (silent errors, which must be 0)
   - check rate

   It can also score OCR-only or model-only runs to compare models fairly.

Runtime: OCR took about 6 seconds per receipt for three variants on this
container's CPU. At 5 to 10 receipts a week this does not matter.

## Phases

Each phase is its own commit and is measured with the eval command.

1. **Measure**: eval command, the IMG_9964 label fix, and the raw model reply
   and OCR text in the details output. No behavior change; this records the
   baseline.
2. **Better OCR**: OCR on every receipt, the date format fix, the new image
   variants and the layout parsers. Report OCR-only accuracy.
3. **Reconcile**: `verified` / `check` statuses and the new summary. Target:
   0 silent errors on the eval set.
4. **Only if the eval shows a need**: line-item prompt with a JSON schema, and
   retesting other models (Gemma 4) through the eval.

## Not Planned

- Jev or any cloud service. Jev is a hosted API, which conflicts with local
  processing.
- Showing the OCR total to the model as a hint. The model tends to agree with
  it, which hides the disagreements this design relies on.
- OCR-specialist vision models such as GLM-OCR. RapidOCR already reads these
  receipts; revisit only if the eval shows OCR misses.
- Larger models. A 3B model plus CPU OCR fits easily on consumer hardware.

## Open Decisions

1. **When only one read succeeds** (for example, the model fails on an SP+
   card but the stamp-line parser succeeds): `check` (recommended), or
   `verified` when a strict layout parser matches on several image variants?
2. **`check` receipts in daily totals**: leave them out, as failures are today
   (recommended), or add the OCR value and mark the day as unverified?
3. **Where the labeled images live**: keep them on the `test-images` branch so
   the eval runs anywhere, or in a git-ignored local folder with only the
   labels committed?
