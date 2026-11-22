"""No model downloads; synthetic recognition contract and retry tests."""
from dataclasses import replace
import sys
import types

import numpy as np
import pytest

from id_ocr import OcrConfig
from id_ocr.engine import EngineError, RawRead, get_engine
from id_ocr.extract import _extract_simple
from id_ocr.paddle_engine import PaddleEngine
from id_ocr.image_io import crop_normalized, ImageError


def test_paddle_missing_weights_fail_before_import(tmp_path):
    engine = PaddleEngine(OcrConfig(backend='paddle',paddle_model_dir=str(tmp_path),use_gpu=False))
    with pytest.raises(EngineError,match='local Arabic') as error:
        engine.reader()
    assert error.value.code == 'language_data_missing'


def test_paddle_cpu_single_model_and_raw_preservation(tmp_path, monkeypatch):
    for name in ('inference.json','inference.pdiparams','inference.yml'):
        (tmp_path/name).touch()
    calls=[]
    class Model:
        def __init__(self, **kwargs): calls.append(kwargs)
        def predict(self, img):
            yield {'rec_text':'١٢٣ س', 'rec_score':.9}
    monkeypatch.setitem(sys.modules,'paddle',types.SimpleNamespace())
    monkeypatch.setitem(sys.modules,'paddleocr',types.SimpleNamespace(TextRecognition=Model))
    engine=PaddleEngine(OcrConfig(backend='paddle',paddle_model_dir=str(tmp_path),use_gpu=False))
    assert engine.reader('ar') is engine.reader('en')
    reads=engine.read_line(np.zeros((40,100),np.uint8),allowlist='0123456789')
    assert reads[0].text == '١٢٣ س' and calls[0]['device']=='cpu'
    assert len(calls)==1 and engine.calls==1


class Fake:
    def __init__(self, text, confidence):self.text=text;self.confidence=confidence;self.calls=0
    def read_line(self, image, *args):
        self.calls+=1
        return [RawRead(self.text,self.confidence,(0,0,10,10))]
    read_tokens=read_line


@pytest.mark.parametrize('value, confidence, expected_calls', [('30001010100011',.99,1),('123',.99,3),('30001010100011',.1,3)])
def test_selective_retry_is_bounded(value,confidence,expected_calls):
    cfg=OcrConfig(backend='paddle',max_rereads=2)
    spec=next(s for s in cfg.regions['front'] if s.name=='national_id')
    engine=Fake(value,confidence)
    result=_extract_simple(spec,np.full((700,1100,3),200,np.uint8),engine,cfg)
    assert engine.calls == expected_calls
    if confidence<.8:assert result.status=='low_confidence'
    if value=='123':assert result.value is None


def test_uncertain_digit_not_silently_removed():
    cfg=OcrConfig(backend='paddle',max_rereads=0)
    spec=next(s for s in cfg.regions['front'] if s.name=='national_id')
    result=_extract_simple(spec,np.ones((700,1100,3),np.uint8),Fake('30001010100011X',.99),cfg)
    assert result.value is None
    assert result.candidates[0].raw.endswith('X')


def test_invalid_dates_and_missing_values():
    cfg=OcrConfig(backend='paddle',max_rereads=1)
    spec=next(s for s in cfg.regions['back'] if s.name=='expiry_date')
    for value in ('2030/02/31','2030/02',''):
        result=_extract_simple(spec,np.ones((700,1100,3),np.uint8),Fake(value,.99),cfg)
        assert result.value is None


def test_crop_rejects_nonfinite_coordinates():
    with pytest.raises(ImageError):crop_normalized(np.zeros((500,800,3)),(0,0,float('nan'),1))


def test_backend_configuration_keeps_easyocr_default():
    from bankwise.api.config import load_ocr_settings
    from bankwise.core.errors import ConfigError
    assert load_ocr_settings({}).backend == 'easyocr'
    configured=load_ocr_settings({'BANKWISE_OCR_BACKEND':'paddle','BANKWISE_OCR_USE_GPU':'0',
                                 'BANKWISE_PADDLE_MODEL_DIR':'/local/model','BANKWISE_OCR_CPU_THREADS':'2'})
    assert not configured.use_gpu and configured.cpu_threads==2
    for env in ({'BANKWISE_OCR_BACKEND':'unknown'},{'BANKWISE_OCR_CPU_THREADS':'100'}):
        with pytest.raises(ConfigError):load_ocr_settings(env)


def test_unavailable_gpu_is_not_silently_enabled(tmp_path,monkeypatch):
    for name in ('inference.json','inference.pdiparams','inference.yml'):(tmp_path/name).touch()
    monkeypatch.setitem(sys.modules,'paddle',types.SimpleNamespace(device=types.SimpleNamespace(is_compiled_with_cuda=lambda:False)))
    monkeypatch.setitem(sys.modules,'paddleocr',types.SimpleNamespace(TextRecognition=lambda **kw:kw))
    cfg=OcrConfig(backend='paddle',paddle_model_dir=str(tmp_path),use_gpu=True)
    with pytest.raises(EngineError) as error:PaddleEngine(cfg).reader()
    assert error.value.code=='gpu_unavailable'
    assert PaddleEngine(replace(cfg,allow_cpu_fallback=True)).reader()['device']=='cpu'
