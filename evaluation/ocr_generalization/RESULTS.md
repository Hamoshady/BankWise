# Egyptian ID OCR generalization evaluation

Tested locally on 2026-11-20 with the existing EasyOCR weights, offline. No model retraining or identity-specific lookup was used. Private images, names, ID numbers and full OCR traces are not included here.

## Original images

Six supplied images form three front/back pairs but represent only two people (one old/new card pair). These results are a small regression set, not an estimate of population accuracy.

| Pair | Files | First name before → after | Expiry before → after | Remaining issues after change |
| --- | --- | --- | --- | --- |
| A | front_sample.jpg / back_sample.jpg | missing → correct, flagged for review | missing → correct | Address line 1 unresolved; issue month single-read; first-name detector disagreement retained |
| B | front face.jpg / back face.jpg | missing → correct | missing → missing | National ID conflict; address line 1 unresolved; issue date missing; printed birth date incorrectly read, flagged low-confidence; religion single-read |
| C | Fron new ID.jpeg / Back new ID.jpeg | correct → correct | correct → correct | Serial misrecognized and rejected; no readable printed birth date (signup can separately derive DOB from structurally valid ID, not claim OCR read it) |

Remaining names, address line 2, job, gender and marital status were reviewed against the supplied images, allowing ordinary Arabic orthographic normalization. National ID matched the supplied image for A and C. A's printed DOB matched. C's first address line matched. No claim that every populated field is correct: B's printed DOB is an explicit counterexample.

## Perturbation checks

Applied in memory to all three pairs: brightness ×0.7, and 80-pixel dark padding plus 3° rotation. Checked first name and expiry only, not all fields.

| Condition | Correct first names | Correct expiries | Wrong nonempty target values |
| --- | --- | --- | --- |
| Original | 3/3 | 2/3 | 0 |
| Dimmed | 3/3 | 2/3 | 0 |
| Padded + tilted | 2/3 | 2/3 | 0 |

The tilted A name becomes unresolved, so tilt robustness is still incomplete. B expiry stays unresolved throughout. Perturbations reuse the same images and do not constitute independent identities.

## Changes

- Conservative contour-based card alignment; preserve unknown boundaries and tightly cropped images; explicit manual crops still take precedence.
- Whole-line first-name recognition fallback, requiring agreement and support from an existing candidate when present. Original disagreements remain visible as low-confidence review flags.
- Locate the Arabic expiry label and read the date to its left. Require full day dates; do not truncate strings, substitute digits, infer expiry from issue date, or accept conflicting valid dates.
- Signup highlights missing and uncertain fields and advises comparison with the document or uploading clearer images.

## Validation and limits

- OCR/generalization/adapter regression tests: 80 passed, 2 opt-in tests skipped. Real-image evaluations above were run separately with actual local weights.
- Streamlit UI tests: 21 passed, including uncertain-field warning before submission.
- Synthetic regression cases cover background protrusions, boundary preservation, different names and dates, incomplete dates, and conflicting date reads.

The existing OCR still has character-recognition errors on clear-looking crops and patterned backgrounds. This is not support for every card design, severe perspective, upside-down images, arbitrary blur, or partially cropped cards. More consented independent identities and annotated examples are needed before claiming broader accuracy. Manual review remains required; OCR does not authenticate a document or its holder.
