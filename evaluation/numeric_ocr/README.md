# Experimental numeric OCR

Production remains EasyOCR. `OcrConfig.numeric_backend` defaults to `None`; the API,
frontend and database are unchanged. All training outputs and identity-bearing data
belong outside this repository. Results from synthetic data are not evidence of
reliable recognition on real identity cards.

## Architecture

Existing OpenCV alignment and template (or optional field detector) localization →
national-ID crop → optional numeric recognizer → existing structural validation →
existing front/back conflict handling. Other fields use the ordinary OCR engine.
The numeric adapter makes one recognition call per side; it never pads or guesses a
missing digit and never resolves disagreeing front/back values by guessing.

Backends:

- `paddle_numeric`: existing local `arabic_PP-OCRv5_mobile_rec` recognizer, reused.
- `yolo_digits`: separate YOLO11n model with ten digit classes, CPU inference,
  left-to-right digit ordering and overlap rejection. Field-detection weights are
  incompatible and deliberately rejected. No automatic weight download.
- `crnn_ctc`: grayscale CNN (16/32/64/96 channels), one bidirectional LSTM with 64
  hidden units per direction, CTC blank + ten digits. 163,467 parameters; CPU
  inference, checkpoint loaded once, serialized inference.

The ordinary EasyOCR adapter remains available and is not replaced. Numeric models
are opt-in through Python configuration only, not enabled by an environment change.

```python
from id_ocr import OcrConfig, IdOcrPipeline
cfg = OcrConfig(use_gpu=False, download_models=False,
                numeric_backend="crnn_ctc",
                numeric_weights="/absolute/private/path/best.pt")
pipeline = IdOcrPipeline(cfg)
pipeline.warmup()
# pipeline.process(front=..., back=...)
```

Numeric model inference currently uses CPU even when training used CUDA. Use the
existing optional Paddle/YOLO environments; no new mandatory dependency is added.
The core numeric helper itself does not import Torch or load a model by default.

## Reproduce training

Use a Python environment with Torch, OpenCV, NumPy and Pillow; YOLO training also
requires the existing optional Ultralytics package. Set `PYTHONPATH=src` (plus any
local optional dependency target) and choose output directories outside the repo.

```bash
python scripts/numeric/generate.py "$PRIVATE/synthetic" --train 5000
python scripts/numeric/train_crnn.py --data "$PRIVATE/synthetic" \
  --output "$PRIVATE/crnn-training" --epochs 20
# Continue from saved weights into a NEW output directory. Optimizer is reset.
python scripts/numeric/train_crnn.py --data "$PRIVATE/synthetic" \
  --initial-checkpoint "$PRIVATE/crnn-training/best.pt" \
  --output "$PRIVATE/crnn-training-continued" --epochs 15
python scripts/numeric/train_yolo.py --data "$PRIVATE/synthetic/data.yaml" \
  --weights "$PRIVATE/verified-yolo11n.pt" --output "$PRIVATE/yolo-training" --epochs 20
```

The synthetic generator records fonts, hashes, seed and provenance. Locally installed
Noto fonts are used under SIL OFL 1.1; the license is copied alongside the data.
Training uses only synthetic labels. The existing Thndr dataset supplies field
boxes, not individual-digit boxes or reviewed numeric transcripts.

Training scripts reject existing output directories. CRNN early-stops on synthetic
validation accuracy/CER; saved interrupted weights can be continued, but the original
checkpoint did not preserve optimizer or RNG state, so this is not an exact resume.
YOLO uses 2,000 synthetic training crops, 512-pixel input, batch 16, frozen first ten
layers, no mirror/mosaic, seed 731 and a 20-epoch budget on the 4-GB RTX 3050 Laptop.
The CRNN uses 5,000 synthetic training crops, batch 64 and seed 712.

## Benchmark

`benchmark.py` accepts a private manifest with `samples`, each containing a
pseudonymous `id`, `front`, `back`, and `truth.national_id`. Never commit that file.
Use identical images, CPU thread count, Docker image and CPU/RAM limits for all runs.
Keep training and benchmarking separate in time. Warm inference excludes loading and
one warmup. Reports contain aggregate errors and sample aliases, never recognized
or ground-truth digits.

```bash
python scripts/numeric/benchmark.py --manifest "$PRIVATE/manifest.json" \
  --engine paddle_numeric --paddle-dir "$PADDLE_MODEL" --easy-dir "$EASY_MODELS" \
  --grid --output "$PRIVATE/selection.json"
python scripts/numeric/benchmark.py --manifest "$PRIVATE/manifest.json" \
  --engine crnn_ctc --weights "$PRIVATE/crnn-training-continued/best.pt" \
  --paddle-dir "$PADDLE_MODEL" --easy-dir "$EASY_MODELS" \
  --selection "$PRIVATE/selection.json" --output "$PRIVATE/crnn-results.json"
```

Engine choices: `easyocr`, `paddle`, `paddle_numeric`, `yolo_digits`, `crnn_ctc`.
Grid selection deliberately uses aliases B/C only, with A reserved from this tuning;
this split is specific to the original diagnostic set. For a new dataset, replace
that split explicitly before any selection. These cards have been used previously,
so A is not a pristine final test. All six recognition crops must have identical
`crop_sha256` across the five engine runs.

Raw numeric results use a shared 0.85 confidence review threshold and structural
validation. This threshold is not calibrated across models; neither a high score
nor a structurally valid number proves correctness. Full-pipeline recognition adds
existing retries/merging for ordinary OCR, with Paddle for nonnumeric fields in
numeric experiments. Pair latency therefore is not the numeric model latency.
Manual review at pair level also includes failures of nonnumeric fields.

## Real annotation workflow

`annotations.py blank --images PRIVATE_CROPS --output PRIVATE_MANIFEST` creates empty
labels for human transcription. `annotations.py audit PRIVATE_MANIFEST` checks
review flags, explicit identity groups, split leakage, exact duplicates and any
provided digit boxes. It never fills labels with OCR predictions. Near duplicates
still require visual review. Obtain permission for real identities and keep all
images/transcripts private; independent identity-disjoint real validation and test
sets are needed before model selection.

Verified initialization: official Ultralytics YOLO11n COCO checkpoint, SHA256
`0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
Existing Ultralytics licensing terms still apply; no new model was downloaded.

## Files changed for this experiment

- `src/id_ocr/numeric.py`: bounded preprocessing, common numeric result/validation,
  Paddle and digit-YOLO adapters, local weight checks and inference reuse.
- `src/id_ocr/numeric_crnn.py`: compact model, CTC decoding and CPU adapter.
- `src/id_ocr/config.py`: optional numeric settings, disabled by default.
- `src/id_ocr/pipeline.py` / `extract.py`: opt-in numeric crop recognition, preserving
  ordinary behavior, public fields and existing front/back conflict handling.
- `scripts/numeric/`: synthetic generation, human annotation audit, bounded training,
  controlled benchmark and aggregate-only report generation.
- `tests/test_numeric_ocr.py`: numeric contracts and CPU/model reuse tests.
- `evaluation/numeric_ocr/`: documentation and privacy-safe measured results.

For the resumed run, the external benchmark launcher was corrected to the mounted
`New Volume1` path and the continued CRNN checkpoint. Project scripts use arguments
rather than embedding that machine-specific media path. Models, real images and
transcripts remain outside Git. Existing optional dependency versions were reused;
no dependency file, public API schema or database change was necessary.
