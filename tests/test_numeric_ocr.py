"""Synthetic-only contracts for the disabled-by-default numeric experiment."""
from types import SimpleNamespace
import numpy as np
import pytest
from id_ocr import OcrConfig
from id_ocr.engine import EngineError
from id_ocr.numeric import numeric_result, digits_from_boxes, prepare, pad_field_box, create_numeric, PaddleNumeric, YoloDigits

SYNTHETIC = '30001010100011'

def test_default_unchanged():
    cfg=OcrConfig()
    assert cfg.backend=='easyocr'
    assert create_numeric(cfg) is None

@pytest.mark.parametrize('text', [SYNTHETIC, '٣٠٠٠١٠١٠١٠٠٠١١', '3000 1010100011'])
def test_digit_normalization(text):
    r=numeric_result(text,'test',.99)
    assert r['value']==SYNTHETIC and r['valid_structure'] and not r['requires_review']
    assert r['raw_text']==text

@pytest.mark.parametrize('text', [SYNTHETIC[:-1],SYNTHETIC+'1',SYNTHETIC[:3]+'O'+SYNTHETIC[4:],''])
def test_never_fabricate_digits(text):
    r=numeric_result(text,'test',.99)
    assert r['value'] is None and r['requires_review']

@pytest.mark.parametrize('text', ['10001010100011','30002310100011'])
def test_invalid_structure(text):
    r=numeric_result(text,'test',.99)
    assert not r['valid_structure'] and r['requires_review']

@pytest.mark.parametrize('confidence', [None,.1])
def test_low_confidence(confidence):
    assert numeric_result(SYNTHETIC,'test',confidence)['requires_review']

def test_order_and_overlap():
    boxes=[(i*12,0,i*12+10,20,int(c),.99) for i,c in enumerate(SYNTHETIC)]
    assert digits_from_boxes(boxes[::-1])['value']==SYNTHETIC
    boxes[1]=(0,0,10,20,0,.99)
    r=digits_from_boxes(boxes)
    assert r['value'] is None and 'overlapping_digits' in r['flags']

def test_missing_weights(tmp_path):
    with pytest.raises(EngineError):YoloDigits(tmp_path/'missing.pt').warmup()

def test_paddle_reuses_engine_one_read():
    class Engine:
        calls=0
        def read_line(self,*args):
            self.calls+=1
            return [SimpleNamespace(text=SYNTHETIC,conf=.99)]
    engine=Engine();r=PaddleNumeric(engine)
    for _ in range(2):assert r.recognize(np.zeros((32,200,3),np.uint8))['value']==SYNTHETIC
    assert r.engine is engine and engine.calls==2

def test_crop_bounds_and_limits():
    assert pad_field_box((0,0,1,1),.1)==(0,0,1,1)
    with pytest.raises(ValueError):pad_field_box((0,0,1,1),.5)
    with pytest.raises(ValueError):prepare(np.zeros((2100,2100),np.uint8))
    with pytest.raises(ValueError):prepare(np.zeros((0,0),np.uint8))

def test_crnn_cpu_reuse_and_ctc(tmp_path):
    torch=pytest.importorskip('torch')
    from id_ocr.numeric_crnn import NumericCRNN, CrnnRecognizer,ctc_decode
    indices=[2,2,0,2,0,3,3]
    scores=torch.full((len(indices),11),-50.)
    for i,c in enumerate(indices):scores[i,c]=0.
    assert ctc_decode(scores)[0]=='112'
    model=NumericCRNN();path=tmp_path/'test.pt'
    torch.save({'architecture':'numeric_crnn_v1','state_dict':model.state_dict()},path)
    rec=CrnnRecognizer(path);rec.warmup();loaded=rec.model;rec.warmup()
    assert rec.model is loaded and next(rec.model.parameters()).device.type=='cpu'
    assert isinstance(rec.recognize(np.full((32,200),255,np.uint8))['requires_review'],bool)

@pytest.mark.parametrize('confidence',[float('nan'),float('inf'),1.1,-.1])
def test_invalid_confidence_requires_review(confidence):
    r=numeric_result(SYNTHETIC,'test',confidence)
    assert r['confidence'] is None and r['requires_review']

def test_numeric_integration_conflict_and_no_fallback():
    from id_ocr.extract import extract_side
    from id_ocr.merge import merge_sides
    cfg=OcrConfig(full_image_pass='fallback')
    cfg.regions={side:[next(s for s in specs if s.name=='national_id')] for side,specs in cfg.regions.items()}
    class Forbidden:
        def read_line(self,*args):raise AssertionError('Unexpected ordinary OCR fallback')
        read_tokens=read_line
    class Reader:
        calls=0
        def recognize(self,image):
            self.calls+=1
            return numeric_result(SYNTHETIC if self.calls==1 else '30001010100021','test',.99)
    rec=Reader();image=np.full((500,800,3),200,np.uint8)
    front=extract_side(image,'front',Forbidden(),cfg,rec)
    back=extract_side(image,'back',Forbidden(),cfg,rec)
    result=merge_sides(front,back).fields['national_id']
    assert result.value is None and result.status=='conflict' and rec.calls==2

def test_digit_detector_rejects_field_weights(tmp_path,monkeypatch):
    import sys
    path=tmp_path/'field.pt';path.touch()
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=lambda *a,**k:SimpleNamespace(names={0:'front.national_id'})))
    with pytest.raises(EngineError):YoloDigits(path).warmup()

@pytest.mark.parametrize('raises',[False,True])
def test_digit_detector_cpu_reuse_and_thread_restore(tmp_path,monkeypatch,raises):
    import sys
    state={'threads':4};loads=[];calls=[]
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(get_num_threads=lambda:state['threads'],set_num_threads=lambda n:state.update(threads=n)))
    class Model:
        names={i:str(i) for i in range(10)}
        def predict(self,image,**kw):
            calls.append(kw);state['threads']=2
            if raises:raise RuntimeError('synthetic failure')
            return []
    def factory(*a,**k):loads.append(1);return Model()
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=factory))
    path=tmp_path/'digits.pt';path.touch();rec=YoloDigits(path)
    for _ in range(2):
        if raises:
            with pytest.raises(RuntimeError):rec.recognize(np.zeros((20,200,3),np.uint8))
        else:assert rec.recognize(np.zeros((20,200,3),np.uint8))['requires_review']
        assert state['threads']==4
    assert len(loads)==1 and calls[0]['device']=='cpu' and not calls[0]['save']
