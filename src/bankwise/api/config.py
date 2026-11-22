from dataclasses import dataclass

from bankwise.core.errors import ConfigError


@dataclass(frozen=True)
class OcrSettings:
    model_dir: str | None = None
    use_gpu: bool = True
    allow_cpu_fallback: bool = False
    backend: str = "easyocr"
    paddle_model_dir: str | None = None
    cpu_threads: int = 4
    yolo_weights: str | None = None


def load_ocr_settings(environ):
    def boolean(name, default):
        value = environ.get(name, "1" if default else "0")
        if value not in {"0", "1"}:
            raise ConfigError(f"{name} must be 0 or 1")
        return value == "1"
    backend = environ.get('BANKWISE_OCR_BACKEND', 'easyocr')
    if backend not in {'easyocr', 'paddle'}:
        raise ConfigError('BANKWISE_OCR_BACKEND must be easyocr or paddle')
    try:
        threads = int(environ.get('BANKWISE_OCR_CPU_THREADS', '4'))
    except ValueError:
        raise ConfigError('BANKWISE_OCR_CPU_THREADS must be an integer') from None
    if not 1 <= threads <= 16:
        raise ConfigError('BANKWISE_OCR_CPU_THREADS must be between 1 and 16')
    return OcrSettings(environ.get("BANKWISE_OCR_MODEL_DIR") or None,
                       boolean("BANKWISE_OCR_USE_GPU", True),
                       boolean("BANKWISE_OCR_ALLOW_CPU_FALLBACK", False), backend,
                       environ.get("BANKWISE_PADDLE_MODEL_DIR") or None, threads, environ.get("BANKWISE_YOLO_WEIGHTS") or None)
