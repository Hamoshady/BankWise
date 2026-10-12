# Bankwise AI

Identity document OCR and validation using a configurable vision-language model provider.

## Requirements

- Python 3.10
- Git
- Docker Desktop (optional)
- Gemini API key for cloud processing

## Local Setup

Create a virtual environment:

```powershell
py -3.10 -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the project and development dependencies:

```powershell
python -m pip install -e ".[dev]"
```

## Configuration

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Set your API key in `.env` using `BANKWISE_VLM_API_KEY`.

Review `configs/identity.yaml` to configure the provider and model.

**Privacy:** When cloud processing is enabled, document images may be sent to the configured external provider. Process only images you are authorized to use.

## Run Locally

```powershell
python scripts/run_identity.py "path\to\front.jpg" "path\to\back.jpg"
```

Output is redacted by default. Use `--show-pii` only when necessary and handle sensitive output securely.

## Run with Docker

Build the image:

```powershell
docker compose build
```

If your images are in `data/front.jpg` and `data/back.jpg`, run:

```powershell
docker compose run --rm bankwise python scripts/run_identity.py /app/data/front.jpg /app/data/back.jpg
```

Configure `BANKWISE_VLM_API_KEY` in `.env` before cloud processing.

## Run Tests

```powershell
python -m pytest
```

## Security Notes

- Never commit API keys or `.env`.
- Never commit identity document images or sensitive output files.
- Keep real identity data out of public repositories.
- Verify permissions before sharing results containing personal information.