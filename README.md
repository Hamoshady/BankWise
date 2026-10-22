# BankWise AI - Egyptian National ID OCR

Local OCR pipeline for Egyptian national ID cards using EasyOCR.

## Tests
python -m pytest -q

## Run
python -m id_ocr.cli run --front front.jpg --back back.jpg

## Privacy
Terminal output is redacted by default. Treat unredacted JSON and ID images as sensitive data.
