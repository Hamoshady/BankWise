# Thndr pre-download audit — 2026-11-21

Historical pre-download audit. The export has since been supplied and experimental training started; see RESULTS.md for the completed audit and measurements.
EasyOCR remains the serving default. No inference was sent to external services.

## Evidence

- Project: https://universe.roboflow.com/thndr-ovgh9/thndr-national-card
- Page displays MIT, 971 source images, 16 classes, four dataset versions.
  No project description is published. The export's license file and provenance
  have not been obtained. A license label does not establish participant consent.
- Version 1 page: https://universe.roboflow.com/thndr-ovgh9/thndr-national-card/dataset/1
  lists 650 images: 606 train, 28 valid, 16 test; auto-orient, stretch to 512x512,
  six outputs per training example, grayscale/saturation/brightness augmentation.
  These are version-1 settings, not verified version-4 settings. Version 4 is
  listed as 4363 images and 512x512 stretching; do not equate this with identities.
- Three public thumbnails were downloaded to the private local audit directory
  and visually inspected. They include front/back cards, a hand-held background,
  different orientations and visibly different image conditions. Thumbnail size
  prevents judging source text sharpness. No box overlays were available, so
  annotation tightness/completeness could not be assessed.
- API request for project version 4 returned HTTP 401: API key required.
  No ROBOFLOW_API_KEY is configured, and no export ZIP was found in Downloads.
  Direct dataset HTML retrieval also returned HTTP 403. No access controls were bypassed.
- Local GPU reported RTX 3050 Laptop, 4096 MiB VRAM. Training feasibility still
  requires checking the isolated CUDA/PyTorch environment and actual batch memory.

## Prepared tooling

`scripts/audit_yolo_dataset.py` audits an extracted Roboflow YOLO detection export:
class counts, image dimensions, decode failures, missing/empty labels, invalid
normalized boxes and exact decoded-image duplicates across splits. It never
uploads, trains, modifies the dataset or automatically approves it. Reports omit
filenames/pixels. Synthetic test passed (1 test).

Run after local extraction:

    python scripts/audit_yolo_dataset.py /private/thndr-export --output /private/thndr-audit.json

Exact pixel hashes cannot establish absence of near-duplicate/identity leakage.
Group source photos, augmentations, old/new versions and front/back of the same
identity before splitting. Preserve an independent, unaugmented test set.

## Outstanding before training

1. Obtain YOLOv11 or YOLOv8 detection ZIP with data.yaml, images, labels and README/license.
2. Inspect class box overlays on a stratified front/back sample, not only thumbnails.
3. Review provenance and original resolution; prefer unaugmented originals where available.
4. Group duplicates/identities and build documented train/validation/test splits.
5. Resolve class mapping: ID needs front/back context; Job1/Job2 are separate lines;
   Gender/Religion/Status are separate fields while the current OCR template has
   a combined attributes row. Do not naively rename classes and drop information.
6. Train/evaluate YOLO11n in isolation, then integrate field-level crops and compare
   Paddle-only vs Paddle+YOLO under identical CPU conditions and independent labels.

No new model accuracy, detector quality, or combined latency is reported yet.
