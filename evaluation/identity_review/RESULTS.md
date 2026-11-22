# Identity review and OCR verification — 2026-11-17

- 764 unit, backend integration and deterministic OCR tests passed; 2 opt-in tests skipped.
- 15 Streamlit AppTest tests passed, including pending-to-active refresh, reviewer confirmation and cleared evidence.
- 3 real EasyOCR integration tests passed on synthetic images.
- Live local API accepted a synthetic image-pair submission. Streamlit AppTest signed in as the separate reviewer, displayed evidence, approved the request and signed in as the customer. The customer dashboard loaded without permission errors. This used API multipart upload plus UI review, not a visual browser/file-picker test. The zero-balance QA account was then closed and disabled; its audit history remains.

## Authorized private-card experiment

No card image, personal value, candidate text or document identifier is included in this report or checked-in tests. One supplied card pair was processed locally before and after the change. Of the 12 UI fields, nonempty directly-read values increased from 8 to 11. First name, address line 1 and expiry date changed from unresolved to agreement between variants; other-name confidence improved to `ok`. This measures completeness and internal consistency on one sample, not general OCR accuracy or authenticity.

Arabic text fields now use detection before recognition. The first-name crop includes the full text height; the expiry crop is wider. Numeric validation and disagreement rejection were not relaxed; no personal values were hardcoded. Birth date is not separately printed in this sample and remains unread as a printed field. A birth date derived from a structurally valid national ID is displayed separately with its source. The serial field remains unreliable and is not claimed as repaired.

Review submission is explicit and separate from extraction. Evidence is encrypted with a dedicated deployment key, visible only to the reviewer role, removed after a decision, and inaccessible after a seven-day deadline. Expired evidence is removed on subsequent status/list/submission cleanup. Approval never credits funds. Customer self-approval, repeated decisions, stale approval and unauthorized reviewer access are rejected.
