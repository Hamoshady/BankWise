"""Optional local, single-model recognition backend. No detection or remote OCR."""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time

import cv2

from .engine import EngineError, RawRead

MODEL_NAME = 'arabic_PP-OCRv5_mobile_rec'


class PaddleEngine:
    supports_detection = False

    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = 0
        self.inference_ms = 0.0
        self._model = None
        self._lock = threading.RLock()
        self._device = 'cpu'

    def reader(self, key='ar'):
        # Both logical readers use the SAME Arabic recognition model; no EasyOCR import.
        with self._lock:
            if self._model is not None:
                return self._model
            directory = Path(self.cfg.paddle_model_dir or '')
            if not self.cfg.paddle_model_dir or not all((directory / name).is_file() for name in
                    ('inference.json', 'inference.pdiparams', 'inference.yml')):
                raise EngineError('language_data_missing', 'Install the local Arabic PaddleOCR model before startup.')
            os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
            os.environ.setdefault('DISABLE_MODEL_SOURCE_CHECK', 'True')
            try:
                import paddle
                from paddleocr import TextRecognition
            except ImportError:
                raise EngineError('paddle_missing', 'Install requirements-ocr-paddle.txt in the OCR environment.') from None
            if self.cfg.use_gpu:
                if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
                    self._device = 'gpu:0'
                elif not self.cfg.allow_cpu_fallback:
                    raise EngineError('gpu_unavailable', 'Paddle GPU is unavailable; configure CPU inference.')
            try:
                self._model = TextRecognition(model_name=MODEL_NAME, model_dir=str(directory),
                    device=self._device, cpu_threads=self.cfg.cpu_threads, enable_mkldnn=False)
            except Exception:
                raise EngineError('model_init_failed', 'Paddle recognition model initialization failed.') from None
            return self._model

    def read_line(self, img, reader_key='ar', allowlist=None):
        # Never discard characters using allowlist: that can hide uncertain OCR digits.
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        with self._lock:
            model = self.reader(reader_key)
            start = time.perf_counter()
            try:
                results = list(model.predict(img))
                reads = []
                for result in results:
                    text = result.get('rec_text', '')
                    if text and text.strip():
                        reads.append(RawRead(text, float(result.get('rec_score', 0)), (0, 0, img.shape[1], img.shape[0])))
                return reads
            except Exception:
                raise EngineError('ocr_failed', 'Paddle recognition inference failed.') from None
            finally:
                self.calls += 1
                self.inference_ms += (time.perf_counter() - start) * 1000

    def read_tokens(self, img, reader_key='ar', allowlist=None, **kwargs):
        # Input is a known single field/row. The model preserves within-line order.
        return self.read_line(img, reader_key, allowlist)

    def info(self):
        return {'backend': 'paddle', 'model': MODEL_NAME, 'readers': {'ar': self._device},
                'ocr_calls': self.calls, 'inference_ms_total': round(self.inference_ms, 1),
                'field_detection': 'external_to_recognizer'}

    def cleanup(self):
        with self._lock:
            self._model = None
