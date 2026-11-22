"""Small digit-only CRNN; PyTorch is imported only for this optional backend."""
from pathlib import Path
from threading import RLock
import cv2
import numpy as np
import torch
from torch import nn
from .engine import EngineError
from .numeric import numeric_result, prepare


class NumericCRNN(nn.Module):
    def __init__(self):
        super().__init__()
        layers=[];cin=1
        for i,cout in enumerate((16,32,64,96)):
            layers.extend([nn.Conv2d(cin,cout,3,padding=1),nn.BatchNorm2d(cout),nn.ReLU()])
            if i<2:layers.append(nn.MaxPool2d(2))
            else:layers.append(nn.MaxPool2d((2,1)))
            cin=cout
        self.cnn=nn.Sequential(*layers)
        self.rnn=nn.LSTM(96,64,bidirectional=True)
        self.head=nn.Linear(128,11) # blank=0; digits 0..9 use class indices 1..10
    def forward(self,x):
        x=self.cnn(x).mean(2).permute(2,0,1)
        x,_=self.rnn(x)
        return self.head(x).log_softmax(2)


def crnn_image(image):
    image=prepare(image)
    g=image if image.ndim==2 else cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    width=min(512,max(160,round(g.shape[1]*32/g.shape[0])))
    g=cv2.resize(g,(width,32),interpolation=cv2.INTER_AREA)
    out=np.full((32,512),255,np.uint8);out[:,:width]=g
    return torch.from_numpy(out.astype(np.float32)/127.5-1).unsqueeze(0),width//4


def ctc_decode(log_probs,length=None):
    p=log_probs.detach().exp();scores,indices=p.max(-1)
    indices=indices[:length].tolist();scores=scores[:length].tolist()
    text=[];confidence=[];last=None
    for idx,score in zip(indices,scores):
        if idx and idx!=last:text.append(str(idx-1));confidence.append(score)
        last=idx
    return ''.join(text),min(confidence) if confidence else None


class CrnnRecognizer:
    def __init__(self, weights):
        self.weights=Path(weights or '');self.model=None;self.lock=RLock()
    def warmup(self):
        with self.lock:
            if self.model is not None:return
            if not self.weights.is_file():raise EngineError('numeric_weights_missing','Local CRNN weights are required.')
            checkpoint=torch.load(self.weights,map_location='cpu',weights_only=True)
            if checkpoint.get('architecture')!='numeric_crnn_v1':raise EngineError('numeric_weights_invalid','Unsupported CRNN architecture.')
            model=NumericCRNN();model.load_state_dict(checkpoint['state_dict']);model.eval();self.model=model
    def recognize(self,image):
        with self.lock,torch.inference_mode():
            self.warmup();x,length=crnn_image(image)
            raw,confidence=ctc_decode(self.model(x.unsqueeze(0))[:,0],length)
            return numeric_result(raw,'crnn_ctc',confidence)
