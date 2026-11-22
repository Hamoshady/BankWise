# Validation

- Docker CPU-only run: **194 passed, 2 skipped** in 3.32 seconds.
- Tests: `test_numeric_ocr.py`, `test_ocr.py`, `test_paddle_ocr.py`,
  `test_field_detector.py`, `test_ocr_generalization.py`,
  `unit/core/test_id_ocr_adapter.py`, `unit/core/test_identity_processing.py`.
- Includes real Torch CRNN construction/load/reuse on CPU; synthetic CTC repeat and
  blank decoding; numeric normalization, wrong length, invalid dates/century,
  invalid/low confidence, digit ordering/overlap, weight class compatibility,
  CPU YOLO settings/thread restoration, and front/back conflicts.
- Existing optional real-image EasyOCR tests are skipped in this unit suite;
  real-card validation is performed separately by the controlled benchmark.
- Syntax compilation passed for numeric modules and scripts.
- The benchmark container uses current source mounted read-only. An initial test
  attempt mixed current OCR source with an older baked API module; mounting the
  complete current source and test configuration resolved that environment issue.
- No production backend change, database migration, UI edit or service restart.
