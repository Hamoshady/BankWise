"""EasyOCR engine wrapper: one-time reader initialisation, GPU verification, timing.

Readers are created once per process and reused (see ``get_engine``). The
engine never changes the Python environment: if the GPU cannot be used it
raises an actionable ``EngineError`` unless ``cfg.allow_cpu_fallback`` is set.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .config import OcrConfig
from .preprocess import pad_line

log = logging.getLogger("id_ocr.engine")

# EasyOCR language sets. The Arabic recognition model also covers digits and
# latin letters; a separate English reader is used for the latin serial number.
READER_LANGS = {"ar": ["ar", "en"], "en": ["en"]}


class EngineError(Exception):
    """Initialisation/inference failure with a user-safe code and actionable message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class RawRead:
    text: str
    conf: float
    bbox: Tuple[int, int, int, int]  # x0, y0, x1, y1 in the coordinates of the image passed in


class OcrEngine:
    def __init__(self, cfg: Optional[OcrConfig] = None):
        self.cfg = cfg or OcrConfig()
        self._readers: Dict[str, Any] = {}
        self._lock = threading.Lock()          # one OCR call at a time on the GPU
        self._gpu: Optional[bool] = None
        self.fallback_reason: Optional[str] = None
        self.calls = 0
        self.inference_ms = 0.0

    # ------------------------------------------------------------------ device
    def _resolve_gpu(self) -> bool:
        if self._gpu is not None:
            return self._gpu
        if not self.cfg.use_gpu:
            self._gpu = False
            return False
        try:
            import torch
        except ImportError as exc:
            raise EngineError("torch_missing", "PyTorch is not installed in the active Python environment.") from exc
        if torch.cuda.is_available():
            self._gpu = True
            return True
        built_with = getattr(torch.version, "cuda", None)
        if built_with is None:
            reason = (f"The installed PyTorch ({torch.__version__}) is a CPU-only build. "
                      "Install the CUDA build matching your driver from pytorch.org; this package will not do it for you.")
        else:
            reason = (f"PyTorch {torch.__version__} (CUDA {built_with}) cannot see a GPU. Check `nvidia-smi`, the NVIDIA "
                      "driver version vs. the CUDA runtime, and that the active interpreter is the GPU environment.")
        if self.cfg.allow_cpu_fallback:
            log.warning("GPU unavailable, using CPU because allow_cpu_fallback=True. %s", reason)
            self.fallback_reason = reason
            self._gpu = False
            return False
        raise EngineError("gpu_unavailable", reason + " Pass --cpu / allow_cpu_fallback=True to run on CPU explicitly.")

    def reader(self, key: str):
        """Return (and lazily create, exactly once) the EasyOCR reader for ``key``."""
        if key in self._readers:
            return self._readers[key]
        gpu = self._resolve_gpu()
        try:
            import easyocr
        except ImportError as exc:
            raise EngineError("easyocr_missing", "EasyOCR is not installed (pip install easyocr).") from exc
        t0 = time.perf_counter()
        try:
            rd = easyocr.Reader(
                READER_LANGS[key], gpu=gpu, model_storage_directory=self.cfg.model_dir,
                download_enabled=self.cfg.download_models, verbose=False,
            )
        except (FileNotFoundError, OSError) as exc:
            raise EngineError("language_data_missing",
                              "EasyOCR model files for the required languages are missing and could not be downloaded. "
                              "Run once with download_models=True and network access, or place the weights in model_dir.") from exc
        except Exception as exc:  # pragma: no cover - library specific failures
            raise EngineError("model_init_failed", f"EasyOCR failed to initialise: {type(exc).__name__}") from exc
        log.info("EasyOCR reader '%s' ready on %s in %.1fs", key, "gpu" if gpu else "cpu", time.perf_counter() - t0)
        self._readers[key] = rd
        return rd

    # --------------------------------------------------------------- inference
    def _timed(self, fn, *a, **kw):
        with self._lock:
            t0 = time.perf_counter()
            try:
                return fn(*a, **kw)
            except Exception as exc:
                raise EngineError("ocr_failed", f"OCR inference failed: {type(exc).__name__}") from exc
            finally:
                self.inference_ms += (time.perf_counter() - t0) * 1000.0
                self.calls += 1

    def read_line(self, img: np.ndarray, reader_key: str = "ar", allowlist: Optional[str] = None) -> List[RawRead]:
        """Recognise a pre-cropped single text line WITHOUT the detection stage.

        Detection (CRAFT) fragments digit strings and drops small dotted zeros;
        recognising the whole crop is markedly more reliable for pre-segmented lines.
        """
        pad = self.cfg.line_padding_px
        g = pad_line(img, pad)
        h, w = g.shape[:2]
        rd = self.reader(reader_key)
        res = self._timed(rd.recognize, g, horizontal_list=[[0, w, 0, h]], free_list=[],
                          allowlist=allowlist, decoder=self.cfg.decoder, detail=1)
        ih, iw = img.shape[:2]
        return [RawRead(text=t, conf=float(c), bbox=(0, 0, iw, ih)) for _, t, c in res if t and t.strip()]

    def read_tokens(self, img: np.ndarray, reader_key: str = "ar", allowlist: Optional[str] = None) -> List[RawRead]:
        """Detection + recognition (use for rows with several words)."""
        rd = self.reader(reader_key)
        res = self._timed(rd.readtext, img, allowlist=allowlist, detail=1, paragraph=False,
                          decoder=self.cfg.decoder)
        out: List[RawRead] = []
        for box, t, c in res:
            if not t or not t.strip():
                continue
            xs, ys = [int(p[0]) for p in box], [int(p[1]) for p in box]
            out.append(RawRead(text=t, conf=float(c), bbox=(min(xs), min(ys), max(xs), max(ys))))
        return out

    # -------------------------------------------------------------- diagnostics
    def info(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"requested_gpu": self.cfg.use_gpu, "cpu_fallback_reason": self.fallback_reason,
                             "ocr_calls": self.calls, "inference_ms_total": round(self.inference_ms, 1),
                             "readers": {k: str(getattr(r, "device", "?")) for k, r in self._readers.items()}}
        try:
            import torch
            d["torch"] = torch.__version__
            d["torch_cuda_build"] = getattr(torch.version, "cuda", None)
            d["cuda_available"] = torch.cuda.is_available()
            if torch.cuda.is_available():
                p = torch.cuda.get_device_properties(0)
                d["gpu_name"] = p.name
                d["gpu_total_vram_mb"] = round(p.total_memory / 2**20)
        except ImportError:
            d["torch"] = None
        return d

    def verify_gpu_usage(self) -> Dict[str, Any]:
        """Prove (not assume) that inference runs on the GPU.

        Evidence collected: reader device string, device of the recognition
        network's parameters, and CUDA peak-memory growth during a real call.
        """
        import torch
        rd = self.reader("ar")
        report: Dict[str, Any] = {"cuda_available": torch.cuda.is_available()}
        try:
            report["recognizer_param_device"] = str(next(rd.recognizer.parameters()).device)
            report["detector_param_device"] = str(next(rd.detector.parameters()).device)
        except Exception:  # pragma: no cover - EasyOCR internals may vary by version
            report["recognizer_param_device"] = "unknown"
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            before = torch.cuda.memory_allocated()
        probe = np.full((96, 480), 255, dtype=np.uint8)
        probe[30:66, 40:440:12] = 0
        t0 = time.perf_counter()
        self.read_line(probe, "ar")
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            report["cuda_peak_mb_during_probe"] = round(torch.cuda.max_memory_allocated() / 2**20, 1)
            report["cuda_allocated_before_mb"] = round(before / 2**20, 1)
        report["probe_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        report["gpu_in_use"] = report.get("recognizer_param_device", "").startswith("cuda") and \
            report.get("cuda_peak_mb_during_probe", 0) > 0
        return report

    def cleanup(self) -> None:
        """Release models and cached GPU memory (call on shutdown or to free VRAM)."""
        self._readers.clear()
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


_ENGINES: Dict[Tuple, OcrEngine] = {}
_ENGINES_LOCK = threading.Lock()


def get_engine(cfg: Optional[OcrConfig] = None) -> OcrEngine:
    """Process-wide engine cache so readers are loaded once and reused."""
    cfg = cfg or OcrConfig()
    key = (cfg.use_gpu, cfg.allow_cpu_fallback, cfg.model_dir, cfg.download_models, cfg.decoder)
    with _ENGINES_LOCK:
        if key not in _ENGINES:
            _ENGINES[key] = OcrEngine(cfg)
        return _ENGINES[key]
