# Thndr YOLO11n + PaddleOCR experiment — 2026-11-21

**Decision: keep EasyOCR as the serving default.** YOLO+Paddle is an experimental
alternative: much faster, improved over template Paddle, but lower complete-pipeline
field exact match than EasyOCR on the supplied small set. No production deployment
or database changes were made. The trained weights are stored privately at
`~/.local/share/bankwise/thndr-audit/training-gpu-v1/train/weights/best.pt`.

## Dataset audit and preparation

The supplied Thndr v4 ZIP contains 4363 stretched 512x512 images, 4071/194/98 in the
original train/valid/test splits, with MIT recorded in README.dataset.txt and
source URL/version in data.yaml. This is publisher-provided licensing metadata;
no participant-consent provenance was supplied. See provenance.json for archive
and official pretrained-weight hashes. Personal images/labels are not committed.

Twelve annotation overlays were inspected: six front and six back examples. The
fields were generally plausible, but images include blur, strong rotation, small
cards/backgrounds and tightly fitted boxes. This sample inspection is not an audit
of every annotation. Full-card Front/Back annotations were inconsistent in coverage.
The initial generic audit reported 101 unsupported rows as invalid boxes; detailed
inspection found polygon rows (83 with 11 values and 18 with 13), **not** out-of-bounds
rectangles. Updated audit.json distinguishes polygon rows. No ordinary rectangle
was out of bounds.

There were 11 exact decoded-image duplicate groups, none spanning original splits.
However, **32 source-name groups spanned original splits**. The preparation removes
brightness variants by source name, retains a middle-brightness representative,
and clusters near-duplicate 63-bit perceptual hashes (Hamming distance <=8) before
deterministic grouped splitting. 840 source-name representatives / 812 clusters
remain: 581 train, 144 validation, 115 test. These counts are not counts of people.
Identity-level separation, rotated near-duplicates and filename collisions cannot
be ruled out; therefore detection metrics are provisional rather than a measure
of unseen-person generalization.

Sixteen polygon rows remain after deduplication and are converted to enclosing
rectangles. Fourteen field classes are retained; sparse Front/Back whole-card
classes are dropped. The export does not label printed birth date or back-side
national ID. Job2 and HusbandName detections are retained for evaluation but not
silently merged into existing public OCR fields.

## Training and detector evaluation

YOLO11n, official COCO initialization, Ultralytics 8.3.228, seed 42, 25 epochs,
512px, batch 8, first 10 backbone layers frozen, no horizontal/vertical mirroring,
AMP disabled. Training plus final evaluation took 213.17 seconds on RTX 3050 Laptop
4 GiB, with approximately 1.13 GiB training GPU allocation observed. Best checkpoint
was chosen by validation fitness; the test split was evaluated after training.
The preliminary CPU attempt was stopped before completing its first epoch; it is
not used in results. GPU packages were isolated from the serving API environment.

On 115 prepared test images: precision 90.95%, recall 87.29%, mAP@50 **91.87%**,
mAP@50–95 **47.94%**. The stricter metric indicates substantial room to improve
box placement. These detection metrics are not OCR text accuracy.

## Initial three-way OCR comparison (see follow-up below for updated YOLO timings)

Six previously supplied images (three front/back pairs, two identities), 39 manually
labeled output fields. Same isolated CPU container image for all three runs,
four CPUs/threads, 4 GiB memory limit, same image inputs and normalization; separate
sequential processes with network disabled. Each has one excluded pipeline warmup
and nine timed pair runs (three repeats). Accuracy counts each pair once. Shared
crop pixel digests match. No real personal values are in these reports.

YOLO is evaluated on every side, with 512x512 stretching to match its training
export; normalized boxes map back to the original processed image for OCR. Generic
production behavior remains detector-off / optional alignment fallback. Confidence
cutoff 0.6 was fixed before comparison, with duplicate/invalid boxes rejected.
Separate back attributes replace combined-row OCR when all are detected. Fields
without a detector result retain template extraction and review behavior.

| Metric | EasyOCR | Paddle templates | YOLO + Paddle |
|---|---:|---:|---:|
| Mean pair latency (s) | 14.16 | 1.18 | 1.52 |
| p95 pair latency (s) | 17.23 | 1.31 | 1.71 |
| Peak process RSS (MiB) | 1271.41 | 703.69 | 777.31 |
| Initialization (s) | 5.53 | 4.21 | 3.22 |
| Exact field accuracy | 71.79% | 51.28% | 66.67% |
| CER (lower is better) | 25.18% | 21.34% | 17.99% |
| Exactly matched fields | 28 / 39 | 20 / 39 | 26 / 39 |
| All labeled fields correct | 0 / 3 | 0 / 3 | 0 / 3 |

These fresh timing figures supersede earlier timings for comparison purposes;
host/runtime differences mean they are not fixed service latency guarantees. Strict
exact match penalizes spelling/spacing/punctuation differences. An explicitly absent
field matching null is counted as correct. Pipeline review decisions do not guarantee
all optional fields are correct. Raw confidence is not a calibrated probability.

## Remaining field failures with YOLO + Paddle

- First and remaining names: 3/3 each; issue and expiry dates: 3/3 each.
- National ID after front/back merging: 1/3; two pairs retain missing/conflicting
  results. The training dataset does not supply a back-NID field locator, so that
  side remains template-based. Do not guess between conflicting numbers.
- Birth date: the two printed dates were not read; the absent date on the third
  card correctly remains null. Derived dates remain separate from printed OCR.
- Address lines: neither line matches exactly on any pair; there are text,
  orthographic and spacing differences. Inspect CER alongside exact match.
- Serial: 2/3 correct; one contains a character error.
- Occupation: 2/3 correct; one remains unresolved.
- Gender: 2/3 correct; one unreadable. Religion and marital status: 3/3 each.
- All three YOLO+Paddle pairs still require review.

Next steps: independently annotate back-NID and printed DOB regions, review tight
boxes and difficult rotations, add consented identities/ground-truth text to an
independent test set, and investigate numeric recognition and conflicts. More epochs
or a detector alone do not guarantee correct OCR. Do not promote this checkpoint
based only on mAP@50 or these two identities.

## Reproduction and changes

- scripts/audit_yolo_dataset.py: local image/label validation and duplicate audit.
- scripts/prepare_thndr.py: polygon conversion, source grouping and prepared splits.
- scripts/train_thndr_yolo.py: bounded isolated training and final detector evaluation.
- src/id_ocr/field_detector.py: explicit supported Thndr field class vocabulary.
- src/id_ocr/pipeline.py / config.py: opt-in always-localize and stretch modes,
  individual back-attribute crops, original-resolution recognition.
- scripts/benchmark_ocr.py: experimental flags and per-side diagnostics.
- Dockerfile.ocr-yolo / requirements-ocr-yolo.txt: optional experiment dependencies.
- Tests cover polygon conversion, invalid boxes, detector mode and original-resolution
  crops, model reuse, and unchanged default-backend behavior.

See ../../docs/ocr_backends.md for commands. Aggregate JSON files in this directory
contain complete field scores and anonymous failure labels; no ID images or text.

Final targeted verification: **174 passed, 2 skipped**. The running API container
was checked read-only: no backend override is configured and its reader uses
EasyOCR. Experimental weights were not deployed to the API/UI.


## Follow-up: YOLO + EasyOCR

**Keep EasyOCR as the serving default.** Adding this detector to EasyOCR reduced
strict output accuracy from 28/39 to 26/39 on the supplied cards, despite improving
speed. YOLO+Paddle also matches 26/39 but is much faster and has lower CER; however
it has fewer correct final national IDs, so equal totals do not mean equal risk.
No complete-card result was fully correct for any configuration.

Same image manifest, crops (pixel hashes verified), trained checkpoint, CPU limits,
confidence threshold and repeat count as above. The existing EasyOCR recognition,
retry and validation policy was retained; this is not a newly optimized recognition-only
EasyOCR implementation. The three-field back-attribute adaptation is shared with
YOLO+Paddle. No serving image/configuration was changed.

During the first attempted run, Ultralytics CPU device selection reset PyTorch's
process-global thread count, slowing the shared EasyOCR reader. That incomplete
run was stopped and excluded. The detector now restores the caller's thread count
in a finally block, including on inference failure. Both YOLO configurations were
then measured again with four PyTorch threads confirmed in their output reports.
Template-only results are the previous controlled runs; they never initialize YOLO
and are unaffected by this fix. CPU timing remains subject to host variability.

| Configuration | Exact fields | Mean pair (s) | p95 (s) | CER | Peak RSS (MiB) |
|---|---:|---:|---:|---:|---:|
| EasyOCR | 28/39 | 14.16 | 17.23 | 25.18% | 1271 |
| YOLO + EasyOCR | 26/39 | 10.20 | 10.88 | 21.10% | 1277 |
| Paddle templates | 20/39 | 1.18 | 1.31 | 21.34% | 704 |
| YOLO + Paddle | 26/39 | 1.40 | 1.57 | 17.99% | 794 |

YOLO+EasyOCR fields (correct out of three): first name 3, remaining names 2,
address line 1 zero, address line 2 zero, national ID 2, birth date 2 (including
one correctly absent value), serial 2, occupation 3, gender 3, religion 3,
marital status 2, issue date 2, expiry date 2. Address exact-match failures can
include spelling and spacing differences, not necessarily wholly unreadable text.

Relative to template EasyOCR, the two lost exact matches were remaining names on
sample A (conflicting OCR variants, therefore no returned value) and marital status
on sample B (nonmatching text). National ID and date correctness did not improve.
Future work could evaluate conservative template-crop fallback for detector-induced
conflicts and canonical closed-vocabulary attribute parsing, using independent data.
Neither change was silently introduced to improve this comparison.

Raw reports: easyocr_yolo.json and updated paddle_yolo.json. The preceding
Paddle+YOLO run is retained as paddle_yolo_initial.json for traceability.

Follow-up verification: **176 tests passed, 2 skipped**, including restoration of
CPU threads after successful and failed detector inference. The UI remains on its
existing EasyOCR deployment.
