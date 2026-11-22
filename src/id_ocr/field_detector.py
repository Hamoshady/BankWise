"""Optional local YOLO field locator. Generic object weights are rejected."""
from pathlib import Path
from threading import RLock
import math
import sys

from .engine import EngineError


class YoloFieldDetector:
    def __init__(self, weights, specs):
        self.weights = Path(weights)
        self.allowed = {f'{s.side}.{s.name}' for rows in specs.values() for s in rows}
        self.allowed.update({'back.gender', 'back.religion', 'back.marital_status',
                             'back.job_line2', 'back.husband_name'})
        self._model = None
        self._lock = RLock()

    def warmup(self):
        with self._lock:
            if self._model is not None:
                return
            if not self.weights.is_file():
                raise EngineError('yolo_weights_missing', 'Custom local ID field weights are required.')
            try:
                from ultralytics import YOLO
            except ImportError:
                raise EngineError('yolo_missing', 'Install the optional Ultralytics dependency.') from None
            model = YOLO(str(self.weights), task='detect')
            names = set(model.names.values())
            if not names or not names <= self.allowed or model.task != 'detect':
                raise EngineError('yolo_classes_invalid', 'Weights must use side.field class names from the configured ID template.')
            self._model = model

    def locate(self, image, side):
        with self._lock:
            self.warmup()
            # Ultralytics CPU device selection changes PyTorch's process-global
            # thread count. Restore it so a shared EasyOCR engine retains its
            # configured CPU budget after detector initialization/inference.
            torch = sys.modules.get('torch')
            threads = torch.get_num_threads() if torch is not None else None
            try:
                results = self._model.predict(image, device='cpu', imgsz=640, conf=.6,
                                              max_det=32, verbose=False, save=False)
            finally:
                if threads is not None and torch.get_num_threads() != threads:
                    torch.set_num_threads(threads)
            found = {}
            ambiguous = set()
            for result in results:
                for box in result.boxes:
                    label = result.names[int(box.cls.item())]
                    if not label.startswith(side + '.'):
                        continue
                    name = label.split('.', 1)[1]
                    coords = tuple(float(v) for v in box.xyxyn[0].tolist())
                    if (len(coords) != 4 or not all(math.isfinite(v) and 0 <= v <= 1 for v in coords)
                            or coords[0] >= coords[2] or coords[1] >= coords[3]):
                        continue
                    if name in found:
                        ambiguous.add(name)
                    found[name] = (coords, float(box.conf.item()))
            return {k: v for k, v in found.items() if k not in ambiguous}
