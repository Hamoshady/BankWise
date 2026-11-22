# OCR comparison — 2026-11-21

**Decision: retain EasyOCR as the serving default.** Paddle is an opt-in experimental
backend. YOLO integration is implemented but disabled because no custom-trained
Egyptian ID field detector weights were available. No YOLO accuracy is claimed.

## Method

Six user-provided images, three front/back pairs representing **two identities**.
Private manually transcribed ground truth: 39 structured fields and 36 crop labels.
Images and ground truth are not committed. Reports contain only aggregate scores,
anonymous sample labels, field names and non-text diagnostic flags.

CPU: Intel Core i5-12450H; Docker limited to four CPUs and 4 GiB RAM; engine threads
four and OpenCV threads one; GPU disabled. Both backends used the same isolated
`bankwise-ai:ocr-benchmark` image, OpenCV 4.10.0, the same source and image pixels,
and fresh processes. Runs were sequential. Inference had networking disabled.
Arabic model: `arabic_PP-OCRv5_mobile_rec`, paddleocr 3.3.2, paddlepaddle 3.2.2,
local checksum-verified weights; MKL-DNN disabled. EasyOCR dependencies come from
the existing `bankwise-ai:semantic` image; the reports include runtime metadata.

Each pipeline had one excluded warmup, followed by three repeats per pair (nine
latency measurements). Accuracy counts each pair only once. Initialization excludes
image processing and downloads. RSS includes the entire Python process/libraries,
not just weights. These are local observations, not production latency guarantees.

## Complete pipeline

| Metric | EasyOCR | PaddleOCR |
|---|---:|---:|
| Warm mean (s) | 15.701 | 1.530 |
| Warm p95 (s) | 18.197 | 1.675 |
| Initialization including framework imports (s) | 8.645 | 4.738 |
| Peak process RSS (MiB) | 1275.9 | 699.0 |
| Exact field accuracy | 71.79% | 51.28% |
| Character error rate (lower is better) | 25.18% | 22.06% |
| Exactly matching fields | 28 / 39 | 20 / 39 |
| All labeled fields correct on a card | 0 / 3 | 0 / 3 |
| National ID after validation/merging | 2 / 3 | 1 / 3 |
| First name / remaining name | 3 / 3 each | 3 / 3 each |
| Issue / expiry date | 2 / 3 each | 3 / 3 each |
| Pipeline decision requires review | 2 / 3 | 3 / 3 |

The prior EasyOCR-only run averaged 11.8 seconds in its original image. The 15.7-second
figure above is the fresh run in the shared comparison image. Neither is a fixed SLA;
runtime versions, initialization and host conditions can affect timing.

The review statistic follows the existing pipeline decision; it does **not** mean
all optional fields were correct. A null ground-truth field counts as an exact
match when the output is also null. Strict normalization handles Unicode, whitespace
and digit scripts but preserves Arabic spelling and punctuation differences.

## Direct recognition on identical crops

Both reports have the same crop pixel SHA-256 digest. The direct test uses the same
original crops and line-recognition interface, with no per-field retries, parsing,
or merging. This isolates recognition more closely; the full pipelines above use
their respective extraction/validation policies.

- easyocr: exact match 25.00%; CER 26.45%; total time for 36 crops 11.810 s (includes first recognition calls).
- paddle: exact match 44.44%; CER 9.68%; total time for 36 crops 3.039 s (includes first recognition calls).

## Failure analysis and next steps

Paddle read all three front national-ID crops correctly, but two back-side reads
were different, structurally valid numbers. The pipeline correctly retained the
cross-side conflict instead of silently choosing a number. On the third pair,
back-side retries conflicted; the front value survived with review flags. High
OCR confidence and a valid numeric format are not proof of correctness.

Paddle read all expiry dates correctly. It missed the gender/religion fields in
the combined attributes region, produced conflicting occupation retries in two
pairs, and failed the printed birth-date formats. Serial and address text still
contain errors. Exact-match address scoring also penalizes spacing/spelling and
punctuation differences; the lower character error rate shows why exact match
alone should not be read as the recognizer being universally worse.

Next: evaluate separate attribute-line crops, investigate numeric/date recognition
and conflicting retries on a larger independently labeled set, and calibrate
review thresholds. Do not weaken conflict checks to improve a benchmark score.
Use a held-out set to avoid tuning only to these two identities.

The optional YOLO adapter runs only when alignment is uncertain, rejects generic
object-class weights, bounds detections, and preserves template fallback/manual
review. It cannot correct OCR characters. To measure Paddle+YOLO, supply trusted
custom-trained weights with the documented side.field class schema and add
`--yolo-weights /local/id-fields.pt` to the benchmark command. No training or generic
YOLO weight download was performed on these personal documents.

## Validation and operation

- 167 targeted OCR, configuration, adapter, identity and transport tests passed;
  two existing tests skipped.
- Five of those tests cover optional detector weights/classes, invalid/ambiguous
  boxes, model reuse and alignment fallback using synthetic data.
- 50 PostgreSQL-backed API/onboarding tests passed using disposable test databases.
- Actual CPU inference completed for both engines on all six images.
- Production API/UI images and the serving EasyOCR default were not replaced.
  No database migration or account/balance mutation was performed.

See [configuration and benchmark instructions](../../docs/ocr_backends.md).
Raw aggregate reports: [EasyOCR](easyocr.json), [PaddleOCR](paddle.json).
