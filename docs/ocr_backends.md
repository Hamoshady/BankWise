# Lightweight OCR backend (opt-in)

The serving default remains EasyOCR. YOLO is disabled/not installed. No database migrations or response field renaming are involved.

Current integration: FastAPI startup creates `IdOcrAdapter` → `IdOcrPipeline`; validated upload bytes are decoded locally. Existing transport limits, identity concurrency limits, encryption/storage, redacted logging, onboarding review and customer approval stay in place.

The experimental path uses the official `arabic_PP-OCRv5_mobile_rec` recognition-only model. Card boundaries are estimated with OpenCV; existing configurable normalized templates locate fields; the same model reads Arabic text, digits and serial crops. The default experimental path has no full-image detector, document VLM, or second recognition model; an optional custom YOLO fallback is described below. Model dictionaries and Latin-serial limitations are evaluated rather than assumed.

First pass uses original crops. Only invalid/low-confidence fields retry with CLAHE/upscaling, at most two additional passes. Conflicting valid reads remain unresolved. The unverified universal serial regex is not applied to this path: serial values require review. Existing EasyOCR behavior remains unchanged for rollback. Metadata in `validation.field_metadata` adds confidence, candidate count and review state without replacing existing statuses. Raw OCR remains accessible only through the explicit sensitive trace, not diagnostic logs. `verified_format` means a format check, not document authenticity or calibrated correctness.

## Installation and configuration

In an isolated Python 3.11 environment with the project dependencies:

```bash
python -m pip install -r requirements-ocr-paddle.txt
python scripts/install_paddle_model.py /absolute/path/to/paddle-arabic
```

The explicit installer pins the official PaddlePaddle Hugging Face model revision and validates SHA-256 hashes. API startup never downloads weights. Missing files fail startup with a safe error. The recognition model is loaded once per worker; logical Arabic/English reader requests reuse that single model. CPU inference is serialized per engine, using four threads by default. No EasyOCR model is initialized on the Paddle path.

Optional API environment (do not enable before acceptance testing):

```text
BANKWISE_OCR_BACKEND=paddle
BANKWISE_PADDLE_MODEL_DIR=/absolute/path/to/paddle-arabic
BANKWISE_OCR_USE_GPU=0
BANKWISE_OCR_CPU_THREADS=4
```

Rollback: `BANKWISE_OCR_BACKEND=easyocr`, with the existing `BANKWISE_OCR_MODEL_DIR`. GPU requires a matching Paddle GPU build and explicit `BANKWISE_OCR_USE_GPU=1`; unsupported GPU fails unless `BANKWISE_OCR_ALLOW_CPU_FALLBACK=1`. GPU is not benchmarked here. Optional CPU dependencies are separate from the main requirements so existing deployment is unaffected.

`Dockerfile.ocr-benchmark` builds an isolated comparison image from the installed semantic API image; mount current `src/id_ocr`, the benchmark script, model directories and private sample manifest read-only for testing. It does not replace the running API. Restrict networking during inference (`--network none`).

## Benchmark

Use `scripts/benchmark_ocr.py --help`. Run each backend in a fresh process with the same hardware, image manifest, CPU/thread limits and repeats:

```bash
PYTHONPATH=src python scripts/benchmark_ocr.py --backend easyocr --model-dir /models/easyocr --manifest /private/cards.json --output /private/easyocr-metrics.json
PYTHONPATH=src python scripts/benchmark_ocr.py --backend paddle --model-dir /models/paddle-arabic --manifest /private/cards.json --output /private/paddle-metrics.json
```

The manifest contains `samples`, each with `id`, `front`, `back`, `truth` (pipeline field names) and `crop_truth` (`front.first_name`, `back.expiry_date`, etc.). An omitted label is not evaluated; null is an explicitly absent field. Do not commit private manifests/images. Reports contain aggregate scores and names of failing fields, not extracted values.

There are two comparisons: direct single-line recognition on identical original crops (checked with a pixel digest), and complete EasyOCR/paddle pipelines with their respective retry policies. Startup, excluded warmup, mean/p95 warm latency, process peak RSS, strict normalized CER/exact matches, per-field correctness, whole-card correctness and review rate are reported. Text normalization preserves Arabic letter distinctions; similar spellings and punctuation can still fail exact match. Repeated timings on three pairs are not independent accuracy samples.

## Limits

- This is an experiment, not a production-readiness claim. More consented independent identities are needed.
- Templates still depend on card layout. Set `OcrConfig.regions`/`load_regions` for documented alternatives; `crop_margin` optionally expands crops, capped at 2% per edge. Default margins are zero to preserve comparison pixels.
- Orientation uses existing geometric alignment/manual rotation, not an automatic upside-down classifier.
- No trained YOLO field detector is available and none is downloaded or trained.
- One model reads whole cropped rows; long/multiline text and large gaps remain recognition risks.
- Shared model confidence thresholds are provisional. A high-confidence wrong read remains possible.
- oneDNN/MKL-DNN acceleration is disabled for the initial compatibility baseline; GPU, batching and acceleration need separate measurements.

## Optional custom YOLO field localization

`BANKWISE_YOLO_WEIGHTS=/absolute/path/to/trusted-id-fields.pt` (or
`OcrConfig(yolo_weights=...)`) enables the optional adapter. Leave unset when
no validated custom weights are available. Install `ultralytics` separately in
an experimental environment; it is not a dependency of ordinary OCR inference.
Only load trusted local weights. There is no automatic model download or training.

Weights must use class names matching `side.field`, for example
`front.first_name`, `front.national_id`, `back.expiry_date`, and
`back.attributes`, according to the configured regions. Generic COCO weights
are rejected. A matching class vocabulary is necessary but does not prove
training quality; evaluate localization on held-out, consented labeled cards.

The detector is loaded once and only runs when automatic alignment is uncertain.
It uses CPU, a 640-pixel inference size, at most 32 detections, no saved images,
and a provisional confidence threshold of 0.6. Invalid boxes and fields with
multiple detections are rejected. Missing locations retain template crops and
remain flagged for review on the Paddle path. Diagnostics report detector
confidence separately from OCR confidence. No detector weights were present
in this workspace, so a Paddle+YOLO accuracy/speed score cannot be reported.

YOLO locates fields; it does not recognize Arabic or correct erroneous digits.
The supplied three pairs (two identities) are suitable for smoke tests, not
for training and independently evaluating a general-purpose ID detector.

## Thndr experiment

Use `scripts/prepare_thndr.py SOURCE_EXPORT PRIVATE_OUTPUT` on the extracted
YOLOv11 export. It preserves license READMEs, converts valid polygon annotations
to enclosing rectangles, removes brightness variants by source name, clusters
perceptually similar representatives before splitting, and drops the inconsistently
annotated full-card classes. It does not establish identity-level independence.

The experiment uses 14 explicit field classes. `back.job_line2` and
`back.husband_name` are detected but are not merged into the existing public job
field. The dataset has no back-national-ID or printed birth-date class. These
retain the existing template OCR. Separate gender/religion/status detections
are recognized individually; when all three are located, the combined row OCR
is skipped.

Training example (private paths, local pretrained weights):

```bash
python scripts/train_thndr_yolo.py --data /private/prepared/data.yaml --weights /private/yolo11n.pt --output /private/training --device 0 --epochs 25
```

`--device cpu` also works, but is substantially slower on the measured machine.
Training freezes the first 10 backbone layers, uses batch 8 / 512px / seed 42,
and disables image mirroring and AMP. All telemetry integrations are disabled.
The GPU experiment reuses local PyTorch 2.13.0+cu130 / torchvision 0.28.0+cu130;
optional packages are isolated from the API. CPU inference uses the optional
`Dockerfile.ocr-yolo` image. Weights remain private and are not committed.

For a controlled detector-vs-template OCR comparison, add
`--yolo-weights /private/best.pt --yolo-always --yolo-stretch-square` to
`scripts/benchmark_ocr.py`. The last flag matches the dataset's stretched
512x512 coordinates; OCR still reads crops from the original processed image.
The ordinary API does not enable these experimental flags or change its backend.
See `evaluation/thndr/RESULTS.md` for measured outcomes and limitations.
