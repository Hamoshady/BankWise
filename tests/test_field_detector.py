"""Synthetic locator tests: no model downloads or personal documents."""
import sys
from types import SimpleNamespace
import numpy as np
import pytest
from id_ocr import OcrConfig
from id_ocr.engine import EngineError
from id_ocr.field_detector import YoloFieldDetector


def test_missing_weights(tmp_path):
    with pytest.raises(EngineError, match='Custom local'):
        YoloFieldDetector(tmp_path/'missing.pt', OcrConfig().regions).warmup()


def test_generic_weights_rejected(tmp_path, monkeypatch):
    path = tmp_path/'generic.pt'
    path.touch()
    monkeypatch.setitem(sys.modules, 'ultralytics', SimpleNamespace(
        YOLO=lambda *a, **k: SimpleNamespace(names={0: 'person'}, task='detect')))
    with pytest.raises(EngineError, match='side.field'):
        YoloFieldDetector(path, OcrConfig().regions).warmup()


def test_locator_filters_sides_invalid_and_ambiguous_boxes(tmp_path, monkeypatch):
    path = tmp_path/'custom.pt'
    path.touch()
    names = {0:'front.first_name', 1:'back.expiry_date', 2:'front.serial', 3:'front.other_names'}
    def box(cls, coords):
        return SimpleNamespace(cls=np.array(cls), xyxyn=np.array([coords]), conf=np.array(.9))
    calls = []
    class Model:
        task = 'detect'
        def predict(self, *a, **kw):
            calls.append(kw)
            return [SimpleNamespace(names=names, boxes=[box(0,[.1,.1,.4,.4]),
                box(1,[.1,.1,.4,.4]), box(2,[.1,.1,.4,.4]), box(2,[.2,.2,.5,.5]),
                box(3,[float('nan'),.1,.4,.4])])]
    model = Model()
    model.names = names
    loads = []
    def factory(*a, **kw):
        loads.append(1)
        return model
    monkeypatch.setitem(sys.modules, 'ultralytics', SimpleNamespace(YOLO=factory))
    detector = YoloFieldDetector(path, OcrConfig().regions)
    for _ in range(2):
        assert set(detector.locate(np.zeros((50,100,3),np.uint8),'front')) == {'first_name'}
    assert len(loads) == 1
    assert calls[0]['save'] is False
    assert calls[0]['max_det'] == 32


@pytest.mark.parametrize('alignment,always,expected_calls', [('rectified',False,0), ('unknown',False,1), ('rectified',True,1)])
def test_pipeline_uses_detector_only_for_uncertain_alignment(monkeypatch, alignment, always, expected_calls):
    from id_ocr.pipeline import IdOcrPipeline
    import id_ocr.pipeline as module
    from id_ocr.engine import RawRead
    class Engine:
        calls = 0
        inference_ms = 0
        supports_detection = False
        def reader(self, *args): pass
        def info(self): return {}
        def read_line(self, *args, **kwargs): return []
        read_tokens = read_line
    cfg = OcrConfig(backend='paddle', use_gpu=False, yolo_always=always)
    pipeline = IdOcrPipeline(cfg, engine=Engine())
    calls = []
    class Detector:
        def warmup(self): pass
        def locate(self, image, side):
            calls.append(side)
            return {'first_name': ((.1,.1,.3,.2), .9)}
    pipeline.field_detector = Detector()
    monkeypatch.setattr(module, 'rectify_card', lambda img: (img, alignment))
    result = pipeline.process(front=np.full((500,800,3),200,np.uint8))
    assert len(calls) == expected_calls
    assert result.public['diagnostics']['yolo'] == 'enabled'
    assert bool(result.public['diagnostics']['field_localization']['front']) == bool(expected_calls)


def test_individual_attributes_use_original_resolution_and_skip_combined_row(monkeypatch):
    import id_ocr.pipeline as module
    from id_ocr.extract import SideResult, FieldResult
    class Engine:
        calls = 0
        inference_ms = 0
        def reader(self, *args): pass
        def info(self): return {}
    pipeline = module.IdOcrPipeline(OcrConfig(backend='paddle', use_gpu=False,
        yolo_always=True, yolo_stretch_square=True, auto_align_card=False), engine=Engine())
    keys = ('gender', 'religion', 'marital_status')
    class Detector:
        def warmup(self): pass
        def locate(self, image, side):
            assert image.shape[:2] == (512,512)
            return {key: ((.1,.1,.2,.2), .9) for key in keys}
    pipeline.field_detector = Detector()
    def side_extract(image, side, engine, cfg):
        assert image.shape[:2] == (500,800)
        assert all(s.name != 'attributes' for s in cfg.regions['back'])
        return SideResult(side, {})
    monkeypatch.setattr(module, 'extract_side', side_extract)
    calls = []
    def field_extract(spec, image, engine, cfg):
        calls.append(spec.name)
        assert image.shape[:2] == (500,800)
        return FieldResult(spec.name, 'back', status='unreadable')
    monkeypatch.setattr('id_ocr.extract._extract_simple', field_extract)
    result = pipeline.process(back=np.full((500,800,3),200,np.uint8))
    assert calls == list(keys)
    assert set(result.sides['back'].fields) == set(keys)


@pytest.mark.parametrize('fail', [False, True])
def test_detector_restores_shared_torch_threads(monkeypatch, fail):
    state = {'threads': 4}
    torch = SimpleNamespace(get_num_threads=lambda: state['threads'],
                            set_num_threads=lambda n: state.update(threads=n))
    monkeypatch.setitem(sys.modules, 'torch', torch)
    class Model:
        def predict(self, *args, **kwargs):
            state['threads'] = 8
            if fail:
                raise RuntimeError('synthetic inference failure')
            return []
    detector = YoloFieldDetector('unused.pt', OcrConfig().regions)
    detector._model = Model()
    if fail:
        with pytest.raises(RuntimeError):
            detector.locate(np.zeros((50,100,3),np.uint8), 'front')
    else:
        assert detector.locate(np.zeros((50,100,3),np.uint8), 'front') == {}
    assert state['threads'] == 4
