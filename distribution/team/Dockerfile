FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    BANKWISE_CONFIG_DIR=/app/configs \
    BANKWISE_OCR_MODEL_DIR=/models \
    BANKWISE_OCR_USE_GPU=0 \
    BANKWISE_OCR_ALLOW_CPU_FALLBACK=0

WORKDIR /app
# CPU wheels are only installed inside this isolated image, never on the developer's machine.
RUN python -m pip install --timeout 120 --retries 10 --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt ./
RUN python -m pip install --timeout 120 --retries 10 --no-cache-dir -r requirements.txt
COPY pyproject.toml ./
COPY src/ ./src/
RUN python -m pip install --timeout 120 --retries 10 --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 bankwise \
    && mkdir -p /app/outputs/audit /app/outputs/secure /models \
    && chown -R bankwise:bankwise /app/outputs /models \
    && chmod 700 /app/outputs/audit /app/outputs/secure
COPY configs/ ./configs/
COPY alembic.ini ./
USER bankwise
EXPOSE 8000
CMD ["uvicorn", "bankwise.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]

USER root
COPY requirements-semantic.txt /tmp/requirements-semantic.txt
RUN python -m pip install --timeout 120 --retries 10 --no-cache-dir -r /tmp/requirements-semantic.txt
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY configs/local_llm_manifest.json /tmp/llm-manifest.json
COPY distribution/team/install_llama.py /tmp/install_llama.py
RUN python /tmp/install_llama.py
COPY distribution/windows-snapshot/serve.py /opt/team/serve.py
COPY distribution/team/initialize.py /opt/team/initialize.py
RUN python -c "from pathlib import Path; p=Path('/app/configs/app.yaml'); p.write_text(p.read_text().replace('single_process: false','single_process: true'))"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 LD_LIBRARY_PATH=/opt/llama
USER bankwise
CMD ["python", "/opt/team/serve.py"]
