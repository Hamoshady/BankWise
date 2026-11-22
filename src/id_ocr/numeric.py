"""Opt-in, local numeric recognition. No correction/invention of uncertain digits."""
from pathlib import Path
from threading import RLock
import re
import sys
import cv2
import numpy as np
from .engine import EngineError
from .normalize import normalize_unicode, to_western_digits
from .validation import decode_national_id

PREPROCESSORS = ('original', 'gray', 'resize', 'clahe', 'border')


def pad_field_box(box, fraction):
    if not np.isfinite(fraction) or not 0 <= fraction <= .1:
        raise ValueError('Numeric crop padding must be between zero and 0.1')
    x0,y0,x1,y1=box;dx=(x1-x0)*fraction
    return max(0.,x0-dx),y0,min(1.,x1+dx),y1


def prepare(image, strategy='original'):
    if image is None or image.size == 0 or image.ndim not in (2, 3):
        raise ValueError('Invalid numeric crop')
    if image.shape[0]*image.shape[1] > 4_000_000:
        raise ValueError('Numeric crop exceeds resource limit')
    if strategy not in PREPROCESSORS: raise ValueError('Unknown numeric preprocessing')
    if strategy == 'original': return image
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if strategy == 'gray': return gray
    if strategy == 'resize': return cv2.resize(gray,None,fx=1.5,fy=1.5,interpolation=cv2.INTER_CUBIC)
    if strategy == 'clahe': return cv2.createCLAHE(clipLimit=1.5,tileGridSize=(4,4)).apply(gray)
    y=max(2,round(image.shape[0]*.1));x=max(2,round(image.shape[1]*.03))
    # A border supplies context but never restores clipped characters.
    return cv2.copyMakeBorder(image,y,y,x,x,cv2.BORDER_REPLICATE)


def numeric_result(raw, backend, confidence=None, flags=()):
    norm=re.sub(r'\s+', '', to_western_digits(normalize_unicode(raw or '')))
    lexical=bool(re.fullmatch(r'[0-9]+',norm))
    length=lexical and len(norm)==14
    errors=list(flags)
    if confidence is not None and (not np.isfinite(confidence) or not 0 <= confidence <= 1):
        confidence=None;errors.append('invalid_confidence')
    if norm and not lexical: errors.append('unexpected_characters')
    if not length: errors.append('length_not_14')
    structural=decode_national_id(norm).valid if length else False
    if length and not structural: errors.append('invalid_structure')
    review=bool(errors) or confidence is None or confidence < .85
    value=norm if length and not any(x in errors for x in ('overlapping_digits','ambiguous_digit_boxes')) else None
    return {'value':value,'raw_text':raw or '', 'normalized_text':norm,'backend':backend,
            'confidence':confidence,'valid_length':length,'valid_structure':structural,
            'requires_review':review,'flags':list(dict.fromkeys(errors))}


class PaddleNumeric:
    def __init__(self, engine, strategy='original'):
        if strategy not in PREPROCESSORS: raise ValueError('Unknown numeric preprocessing')
        self.engine,self.strategy=engine,strategy
    def warmup(self): self.engine.reader('ar')
    def recognize(self,image):
        reads=self.engine.read_line(prepare(image,self.strategy),'ar')
        return numeric_result(' '.join(r.text for r in reads),'paddle_numeric',
                              min((r.conf for r in reads),default=None))


def digits_from_boxes(boxes):
    """boxes = (x0,y0,x1,y1,class_id,confidence); printed NID order is left-to-right."""
    flags=[]; valid=[]
    for row in boxes:
        if len(row)!=6 or not all(np.isfinite(v) for v in row):
            flags.append('ambiguous_digit_boxes');continue
        x0,y0,x1,y1,c,conf=row
        if x1<=x0 or y1<=y0 or c!=int(c) or not 0<=c<=9 or not 0<=conf<=1:
            flags.append('ambiguous_digit_boxes');continue
        valid.append(row)
    valid.sort(key=lambda b:(b[0]+b[2])/2)
    for i,a in enumerate(valid):
        for b in valid[:i]:
            area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
            if area/min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])) > .35:
                flags.append('overlapping_digits')
    return numeric_result(''.join(str(int(b[4])) for b in valid),'yolo_digits',
                          min((b[5] for b in valid),default=None),flags)


class YoloDigits:
    def __init__(self, weights):
        self.weights=Path(weights or '');self.model=None;self.lock=RLock()
    def warmup(self):
        with self.lock:
            if self.model is not None:return
            if not self.weights.is_file():raise EngineError('numeric_weights_missing','Local digit detector weights are required.')
            from ultralytics import YOLO
            model=YOLO(str(self.weights),task='detect')
            if set(model.names.values()) != set('0123456789') or len(model.names)!=10:
                raise EngineError('numeric_classes_invalid','Digit detector must contain exactly 0-9 classes.')
            self.model=model
    def recognize(self,image):
        image=prepare(image)
        with self.lock:
            self.warmup();torch=sys.modules['torch'];threads=torch.get_num_threads()
            try:
                results=self.model.predict(image,device='cpu',imgsz=512,conf=.2,iou=.45,
                    agnostic_nms=False,max_det=64,verbose=False,save=False)
            finally:
                if torch.get_num_threads()!=threads:torch.set_num_threads(threads)
            boxes=[]
            for r in results:
                for b in r.boxes:
                    boxes.append((*b.xyxy[0].tolist(),int(r.names[int(b.cls.item())]),float(b.conf.item())))
            return digits_from_boxes(boxes)


def create_numeric(cfg):
    if cfg.numeric_backend is None:return None
    if cfg.numeric_backend=='paddle_numeric':
        from dataclasses import replace
        from .engine import get_engine
        return PaddleNumeric(get_engine(replace(cfg,backend='paddle')),cfg.numeric_preprocess)
    if cfg.numeric_backend=='yolo_digits':return YoloDigits(cfg.numeric_weights)
    if cfg.numeric_backend=='crnn_ctc':
        from .numeric_crnn import CrnnRecognizer
        return CrnnRecognizer(cfg.numeric_weights)
    raise ValueError('Unknown numeric backend')
