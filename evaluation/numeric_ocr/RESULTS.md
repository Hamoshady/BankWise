# Numeric OCR experiment results

## Decision and scope

Keep EasyOCR as the production default. Numeric adapters remain disabled. These experiments use only six real crops from three front/back pairs representing two identities; they cannot establish generalization. Both identities were seen in prior diagnostic work. Synthetic test scores must not be interpreted as real-card accuracy.

Observed outcome: EasyOCR and Paddle each read 4/6 crops exactly; YOLO digits reads 1/6 and CRNN reads 2/6. The specialized models are smaller/faster but do not improve real-card recognition. Paddle has two incorrect raw sequences that pass the shared confidence/format filter; full-pipeline conflict/review checks prevent automatic acceptance in these pairs.

## Recognition-only comparison

Hardware: Intel Core i5-12450H; training used an RTX 3050 Laptop GPU (4 GB), while every real-card benchmark used CPU. Initialization timings measure explicit model loading, not interpreter/framework import or model downloading.

Same aligned template crops and crop SHA256 for all engines; CPU-only inference, four Torch/OMP threads, one OpenCV thread, Docker CPU limit 4 and RAM limit 4 GiB. Three warm repeats per crop (18 timings); initialization and one warmup excluded. Training had finished before timings. Peak RSS includes the interpreter and imported frameworks, not just model allocations.

| Model | Exact numbers / 6 | Digit CER | Mean / p95 ms | Init s | Peak RAM MiB | Model files MiB |
|---|---:|---:|---:|---:|---:|---:|
| EasyOCR | 4/6 | 3.57% | 271.2 / 360.3 | 3.53 | 928.7 | 284.72 |
| PaddleOCR | 4/6 | 4.76% | 53.8 / 60.1 | 2.86 | 695.9 | 7.77 |
| Paddle numeric (selected original) | 4/6 | 4.76% | 57.3 / 64.9 | 1.91 | 702.6 | 7.77 |
| YOLO11n digits | 1/6 | 15.48% | 13.3 / 18.3 | 0.08 | 343.9 | 5.18 |
| CRNN + CTC | 2/6 | 23.81% | 2.2 / 2.5 | 0.01 | 309.7 | 0.63 |

EasyOCR model files include its Arabic recognizer and loaded CRAFT detector, although the numeric call uses recognition only. The later English serial reader is not included in this numeric size. YOLO/CRNN sizes are inference checkpoints. Paddle size includes local inference files. Raw model confidences are not calibrated.

| Model | Invalid length | Missing digits | Extra digits | Substitutions / GT digit | Incorrect but accepted | Review |
|---|---:|---:|---:|---:|---:|---:|
| EasyOCR | 33.33% | 0.00% | 33.33% | 1.19% | 0.00% | 50.00% |
| PaddleOCR | 0.00% | 0.00% | 0.00% | 4.76% | 33.33% | 16.67% |
| Paddle numeric (selected original) | 0.00% | 0.00% | 0.00% | 4.76% | 33.33% | 16.67% |
| YOLO11n digits | 66.67% | 83.33% | 16.67% | 0.00% | 0.00% | 100.00% |
| CRNN + CTC | 33.33% | 33.33% | 0.00% | 13.10% | 0.00% | 66.67% |

Incorrect-but-accepted means a wrong raw sequence that passes the common structural validator and confidence threshold without an uncertainty flag. Missing/extra rates count crops with at least one deletion/insertion in a minimum edit alignment. CER uses all characters, not only accepted outputs.

## Full-pipeline comparison

Three pairs, three warm repeats each. This includes image handling, all fields and front/back merge. Ordinary EasyOCR/Paddle retain their original retry logic. Numeric experiments use Paddle for other fields, the same template localization, and one numeric read per side; these are not all-field EasyOCR replacement proposals.

| Model | Final correct NID / 3 | Missing/conflicting | NID incorrect but status ok | Whole-card review | Mean / p95 s | Peak RAM MiB |
|---|---:|---:|---:|---:|---:|---:|
| EasyOCR | 2/3 | 33.33% | 0.00% | 66.67% | 13.84 / 16.39 | 1320.9 |
| PaddleOCR | 1/3 | 66.67% | 0.00% | 100.00% | 1.04 / 1.16 | 710.8 |
| Paddle numeric (selected original) | 1/3 | 66.67% | 0.00% | 100.00% | 1.04 / 1.13 | 711.4 |
| YOLO11n digits | 1/3 | 66.67% | 0.00% | 100.00% | 0.98 / 1.16 | 757.6 |
| CRNN + CTC | 2/3 | 0.00% | 0.00% | 100.00% | 0.97 / 1.07 | 729.4 |

A correct final number can still require review. Whole-card review also reflects other fields, expiration and consistency. Experimental single reads retain the existing conservative single_read status.

## Privacy-safe failure diagnostics

A is identity 1; B/C are older/newer cards for identity 2. Each cell lists substitutions / missing / extra characters; OK means exact raw sequence. No identity values or images are included.

| Crop | EasyOCR | PaddleOCR | Paddle numeric (selected original) | YOLO11n digits | CRNN + CTC |
|---|---|---|---|---|---|
| A.front | OK | OK | OK | 0 / 1 / 1 (14 chars) | 5 / 0 / 0 (14 chars) |
| A.back | OK | 1 / 0 / 0 (14 chars) | 1 / 0 / 0 (14 chars) | 0 / 2 / 0 (12 chars) | OK |
| B.front | 0 / 0 / 1 (15 chars) | OK | OK | 0 / 4 / 0 (10 chars) | 3 / 6 / 0 (8 chars) |
| B.back | 1 / 0 / 1 (15 chars) | 3 / 0 / 0 (14 chars) | 3 / 0 / 0 (14 chars) | 0 / 3 / 0 (11 chars) | 2 / 0 / 0 (14 chars) |
| C.front | OK | OK | OK | 0 / 2 / 0 (12 chars) | 1 / 3 / 0 (11 chars) |
| C.back | OK | OK | OK | OK | OK |

Visual inspection found the B.back template crop truncates the last digit at its right edge. Other crops include the sequence but contain faint dotted zeros and varying contrast. Digit substitutions/deletions above are observed errors, not proof of a unique physical cause. No case should be repaired by inferring digits from date/governorate information.

All engines preserve left-to-right numeric order after normalization; no failed sequence exactly matches the reversed truth. Arabic-Indic normalization is covered by synthetic tests. Raw errors are therefore not explained by a simple reversal or digit-script conversion. Structural validation rejects some outputs; valid-looking substitutions can still be wrong. In the CRNN full pipeline, one of three returned values remains wrong and flagged for review (rather than missing); 2/3 final exact matches must not be read as safe automatic acceptance.

## Preprocessing selection

Fifteen configurations (five preprocessing strategies × three horizontal margins) were selected on B/C only: four crops, one identity. The best exact-match setting remained original image, zero extra margin: 3/4 correct, CER 5.36%. Larger margins could lower CER while lowering full-sequence accuracy. Thus crop/preprocessing changes did not reliably fix recognition. A was excluded from this selection but is not an unseen final test.

## Training

### CRNN

```json
{
  "synthetic_only": true,
  "continued_from_checkpoint": true,
  "optimizer_reinitialized": true,
  "seed": 712,
  "epochs_completed": 5,
  "parameter_count": 163467,
  "training_seconds": 18.593551830000024,
  "best_validation_exact_match": 0.995,
  "best_validation_cer": 0.0003637686431429611,
  "synthetic_test": {
    "count": 400,
    "exact_match": 0.9975,
    "cer": 0.0001827151470856934
  },
  "real_generalization_claimed": false
}
```

### YOLO digits

```json
{
  "synthetic_only": true,
  "train_images": 2000,
  "epochs_requested": 20,
  "seed": 731,
  "training_seconds": 393.30655308000007,
  "digit_detection_test": {
    "metrics/precision(B)": 0.9985530261351221,
    "metrics/recall(B)": 0.9995780339579869,
    "metrics/mAP50(B)": 0.9949999999999999,
    "metrics/mAP50-95(B)": 0.8927742128877556,
    "fitness": 0.8927742128877556
  },
  "real_generalization_claimed": false,
  "epochs_completed": 17
}
```

Both models were trained on synthetic numbers only. Available Thndr annotations are field boxes, not digit boxes or reviewed number transcripts. The synthetic split has unique sequences across train/validation/test, but shares font/renderer families; high synthetic scores do not establish domain transfer. CRNN continued saved weights with a reset optimizer, not an exact training-state resume. YOLO used verified local COCO initialization, not the existing field checkpoint.

## Tests, integration and next work

See README.md for architecture, configuration and commands. The final test count is recorded in VALIDATION.md. Numeric models are loaded lazily and reused with inference locks; missing local weights fail clearly. No endpoint/schema/database/frontend changes or production model switch were made. Existing field YOLO checkpoint checksum was verified unchanged.

Next: collect consented real number crops with independently reviewed sequences and, for YOLO, digit boxes. Split by identity; audit near duplicates. Use separate real development and final test sets. Improve realistic typography/background augmentation using development data only, then fine-tune and rerun the controlled benchmark. Do not select a production model based on these two identities.
