"""Bounded synthetic-only CRNN experiment. No production model changes."""
import argparse,json,random,time
from pathlib import Path
import cv2
import numpy as np
import torch
from torch.utils.data import Dataset,DataLoader
from id_ocr.numeric_crnn import NumericCRNN,crnn_image,ctc_decode


def distance(a,b):
    prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for j,y in enumerate(b,1):row.append(min(prev[j]+1,row[j-1]+1,prev[j-1]+(x!=y)))
        prev=row
    return prev[-1]


class Sequences(Dataset):
    def __init__(self,root,rows):self.root,self.rows=root,rows
    def __len__(self):return len(self.rows)
    def __getitem__(self,i):
        row=self.rows[i];im=cv2.imread(str(self.root/row['image']))
        x,length=crnn_image(im);seq=row['sequence']
        if length < len(seq)+sum(a==b for a,b in zip(seq,seq[1:])):raise ValueError('CTC sequence exceeds timesteps')
        return x,length,seq


def collate(batch):
    x,lengths,seqs=zip(*batch)
    targets=torch.tensor([int(c)+1 for seq in seqs for c in seq],dtype=torch.long)
    return torch.stack(x),torch.tensor(lengths),targets,torch.tensor([len(s) for s in seqs]),seqs


def evaluate(model,loader,device):
    model.eval();correct=errors=chars=count=0
    with torch.inference_mode():
        for x,lengths,targets,target_lengths,seqs in loader:
            log_probs=model(x.to(device)).cpu()
            for i,(length,truth) in enumerate(zip(lengths,seqs)):
                raw,_=ctc_decode(log_probs[:,i],int(length));correct+=raw==truth;errors+=distance(truth,raw);chars+=len(truth);count+=1
    return {'count':count,'exact_match':correct/count,'cer':errors/chars}


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--initial-checkpoint',type=Path);p.add_argument('--epochs',type=int,default=20);p.add_argument('--device',default='cuda:0',choices=['cpu','cuda:0']);a=p.parse_args()
    if a.output.exists():p.error('Refusing to overwrite experiment')
    if not 1<=a.epochs<=40:p.error('epochs must be 1..40')
    a.output.mkdir(parents=True,mode=0o700);cv2.setNumThreads(1);torch.set_num_threads(4)
    random.seed(712);np.random.seed(712);torch.manual_seed(712)
    if torch.cuda.is_available():torch.cuda.manual_seed_all(712)
    torch.backends.cudnn.benchmark=False;torch.backends.cudnn.deterministic=True
    rows=json.loads((a.data/'sequences.json').read_text())
    if not all(r.get('synthetic') is True for r in rows):p.error('This experiment accepts synthetic data only')
    groups={s:[r for r in rows if r['split']==s] for s in ['train','val','test']}
    seqsets={s:{r['sequence'] for r in rs} for s,rs in groups.items()}
    assert not(seqsets['train']&seqsets['val'] or seqsets['train']&seqsets['test'] or seqsets['val']&seqsets['test'])
    loaders={s:DataLoader(Sequences(a.data,rs),batch_size=64,shuffle=s=='train',num_workers=0,collate_fn=collate) for s,rs in groups.items()}
    model=NumericCRNN().to(a.device)
    if a.initial_checkpoint:
        checkpoint=torch.load(a.initial_checkpoint,map_location=a.device,weights_only=True)
        if checkpoint.get('architecture')!='numeric_crnn_v1':raise ValueError('Incompatible checkpoint')
        model.load_state_dict(checkpoint['state_dict'])
    optim=torch.optim.Adam(model.parameters(),lr=.001)
    loss_fn=torch.nn.CTCLoss(blank=0,zero_infinity=False)
    best=(float('inf'),float('inf'));stale=0;history=[];start=time.perf_counter()
    if a.initial_checkpoint:
        initial=evaluate(model,loaders['val'],a.device);best=(-initial['exact_match'],initial['cer'])
        torch.save(checkpoint,a.output/'best.pt')
    for epoch in range(a.epochs):
        model.train();total=0
        for x,lengths,targets,target_lengths,seqs in loaders['train']:
            optim.zero_grad();logp=model(x.to(a.device))
            loss=loss_fn(logp,targets.to(a.device),lengths,target_lengths)
            if not torch.isfinite(loss):raise RuntimeError('Nonfinite CTC loss; experiment stopped')
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),5);optim.step();total+=float(loss.detach())
        val=evaluate(model,loaders['val'],a.device);key=(-val['exact_match'],val['cer'])
        row={'epoch':epoch+1,'loss':total/len(loaders['train']),'validation':val};history.append(row);print(json.dumps(row),flush=True)
        if key<best:
            best=key;stale=0
            torch.save({'architecture':'numeric_crnn_v1','state_dict':{k:v.cpu() for k,v in model.state_dict().items()},'seed':712,'synthetic_only':True},a.output/'best.pt')
        else:
            stale+=1
            if stale in (2,4):
                for group in optim.param_groups:group['lr']*=.5
            if stale>=5:break
    checkpoint=torch.load(a.output/'best.pt',map_location=a.device,weights_only=True);model.load_state_dict(checkpoint['state_dict'])
    test=evaluate(model,loaders['test'],a.device)
    report={'synthetic_only':True,'continued_from_checkpoint':bool(a.initial_checkpoint),'optimizer_reinitialized':bool(a.initial_checkpoint),'seed':712,'epochs_completed':len(history),'parameter_count':sum(p.numel() for p in model.parameters()),
            'training_seconds':time.perf_counter()-start,'best_validation_exact_match':-best[0],'best_validation_cer':best[1],
            'synthetic_test':test,'history':history,'real_generalization_claimed':False}
    (a.output/'summary.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='history'}))

if __name__=='__main__':main()
