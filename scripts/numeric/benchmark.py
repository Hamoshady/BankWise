"""Privacy-safe numeric experiments. Manifests/weights stay outside the repository."""
import argparse, hashlib, json, resource, statistics, time
from pathlib import Path
import cv2
import numpy as np
import torch
from id_ocr import OcrConfig, IdOcrPipeline
from id_ocr.engine import get_engine
from id_ocr.alignment import rectify_card
from id_ocr.image_io import load_image, crop_normalized
from id_ocr.numeric import PaddleNumeric, YoloDigits, numeric_result, PREPROCESSORS, pad_field_box
from id_ocr.normalize import normalize_unicode,to_western_digits
from id_ocr.extract import DIGIT_ALLOWLIST


def edit_counts(a,b):
    # tuple: edit distance, substitutions, deletions (missing digits), insertions.
    dp=[[(j,0,0,j) for j in range(len(b)+1)]]
    for i,x in enumerate(a,1):
        row=[(i,0,i,0)]
        for j,y in enumerate(b,1):
            if x==y:row.append(dp[i-1][j-1]);continue
            s=dp[i-1][j-1];d=dp[i-1][j];ins=row[j-1]
            row.append(min((s[0]+1,s[1]+1,s[2],s[3]),(d[0]+1,d[1],d[2]+1,d[3]),(ins[0]+1,ins[1],ins[2],ins[3]+1)))
        dp.append(row)
    return dp[-1][-1]


def metrics(rows):
    n=len(rows);total=sum(len(t) for t,r in rows);ops=[edit_counts(t,r['normalized_text']) for t,r in rows]
    return {'count':n,'exact_match':sum(t==r['normalized_text'] for t,r in rows)/n,
      'cer':sum(x[0] for x in ops)/total,'invalid_length_rate':sum(not r['valid_length'] for t,r in rows)/n,
      'missing_digit_rate':sum(x[2]>0 for x in ops)/n,'extra_digit_rate':sum(x[3]>0 for x in ops)/n,
      'substitutions_per_gt_digit':sum(x[1] for x in ops)/total,
      'incorrect_but_accepted_rate':sum(t!=r['normalized_text'] and not r['requires_review'] for t,r in rows)/n,
      'manual_review_rate':sum(r['requires_review'] for t,r in rows)/n}


def main():
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--engine',choices=['easyocr','paddle','paddle_numeric','yolo_digits','crnn_ctc'],required=True)
    p.add_argument('--paddle-dir',required=True);p.add_argument('--easy-dir',default='/models')
    p.add_argument('--weights');p.add_argument('--selection',type=Path);p.add_argument('--grid',action='store_true')
    p.add_argument('--output',type=Path,required=True);p.add_argument('--repeats',type=int,default=3)
    p.add_argument('--save-private-crops',type=Path)
    a=p.parse_args()
    if not 1<=a.repeats<=10:p.error('repeats must be 1..10')
    cv2.setNumThreads(1);torch.set_num_threads(4)
    cfg=OcrConfig(use_gpu=False,download_models=False,backend='easyocr' if a.engine=='easyocr' else 'paddle',
        model_dir=a.easy_dir,paddle_model_dir=a.paddle_dir,cpu_threads=4,log_level='ERROR')
    selection=json.loads(a.selection.read_text()) if a.selection else {}
    padding=selection.get('crop_padding',0.)
    samples=json.loads(a.manifest.read_text())['samples'];prepared=[];crops=[];contexts=[];digest=hashlib.sha256()
    for sample in samples:
        inputs={s:load_image(sample[s],cfg) for s in ['front','back']};prepared.append((sample,inputs))
        for side,im in inputs.items():
            aligned,status=rectify_card(im);spec=next(s for s in cfg.regions[side] if s.name=='national_id')
            crop=crop_normalized(aligned,pad_field_box(spec.box,padding));digest.update(crop.tobytes())
            contexts.append((aligned,spec.box))
            truth=to_western_digits(normalize_unicode(sample['truth']['national_id']))
            crops.append((sample['id']+'.'+side,truth,crop,status))
            if a.save_private_crops:
                a.save_private_crops.mkdir(parents=True,exist_ok=True,mode=0o700)
                cv2.imwrite(str(a.save_private_crops/(sample['id']+'_'+side+'.png')),crop)
    init=time.perf_counter()
    strategy='original'
    if a.engine=='paddle_numeric' and a.selection:strategy=selection['strategy']
    if a.engine in ('easyocr','paddle','paddle_numeric'):
        engine=get_engine(cfg);engine.reader('ar')
        rec=PaddleNumeric(engine,strategy)
        if a.engine=='easyocr':
            class EasyNumeric:
                def recognize(self,image):
                    reads=engine.read_line(image,'ar',DIGIT_ALLOWLIST)
                    return numeric_result(' '.join(r.text for r in reads),'easyocr',min((r.conf for r in reads),default=None))
            rec=EasyNumeric()
    elif a.engine=='yolo_digits':rec=YoloDigits(a.weights);rec.warmup()
    else:
        from id_ocr.numeric_crnn import CrnnRecognizer
        rec=CrnnRecognizer(a.weights);rec.warmup()
    init=time.perf_counter()-init
    rec.recognize(crops[0][2]) # excluded warmup
    if a.grid:
        if a.engine!='paddle_numeric':p.error('Grid requires paddle_numeric')
        grid={}
        choices=[]
        for pad in (0.,.03,.06):
            for variant in PREPROCESSORS:
                recognizer=PaddleNumeric(engine,variant);rows=[]
                for (ident,t,im,_),(context,box) in zip(crops,contexts):
                    if ident[0] in ('B','C'):
                        rows.append((t,recognizer.recognize(crop_normalized(context,pad_field_box(box,pad)))))
                key=f'{variant}:pad={pad}';grid[key]=metrics(rows);choices.append((pad,variant,key))
        padding,strategy,key=min(choices,key=lambda v:(-grid[v[2]]['exact_match'],grid[v[2]]['cer'],v[0],PREPROCESSORS.index(v[1])))
        a.output.write_text(json.dumps({'strategy':strategy,'crop_padding':padding,'development_cards':['B','C'],'development_identities':1,
            'held_out_from_this_selection':['A'],'not_pristine_test':True,'grid':grid},indent=2))
        print(json.dumps({'selected':strategy,'development_results':grid}));return
    timings=[];rows=[];diagnostics=[]
    for repeat in range(a.repeats):
        for ident,truth,im,align in crops:
            start=time.perf_counter();result=rec.recognize(im);timings.append(time.perf_counter()-start)
            if repeat==0:
                rows.append((truth,result));op=edit_counts(truth,result['normalized_text'])
                diagnostics.append({'sample':ident,'alignment':align,'crop_shape':list(im.shape),
                    'exact_match':truth==result['normalized_text'],'raw_length':len(result['normalized_text']),
                    'edit_counts':{'substitutions':op[1],'missing':op[2],'extra':op[3]},
                    'confidence':result['confidence'],'flags':result['flags'],
                    'reversed_sequence':result['normalized_text']==truth[::-1] and truth!=truth[::-1]})
    recognition_rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
    # End-to-end: ordinary reader retained for nonnumeric fields; conflict merging unchanged.
    if a.engine in ('paddle_numeric','yolo_digits','crnn_ctc'):
        cfg.numeric_backend=a.engine;cfg.numeric_weights=a.weights;cfg.numeric_preprocess=strategy;cfg.numeric_crop_padding=padding
    pipeline=IdOcrPipeline(cfg)
    if cfg.numeric_backend: pipeline.numeric_recognizer=rec
    start=time.perf_counter();pipeline.warmup();pipeline_init=time.perf_counter()-start
    pipeline.process(**prepared[0][1])
    pair_times=[];pair_rows=[];pair_diagnostics=[]
    for repeat in range(a.repeats):
        for sample,inputs in prepared:
            start=time.perf_counter();result=pipeline.process(**inputs);pair_times.append(time.perf_counter()-start)
            if repeat==0:
                value=result.public.get('national_id') or '';truth=to_western_digits(sample['truth']['national_id'])
                status=result.public['validation']['field_status'].get('national_id')
                pair_rows.append((truth,value,status,result.decision.status))
                pair_diagnostics.append({'sample':sample['id'],'exact_match':truth==value,'status':status,
                    'manual_review':result.decision.status!='VERIFIED',
                    'sides':{s:{'status':side.fields['national_id'].status,'flags':side.fields['national_id'].flags}
                             for s,side in result.sides.items()}})
            print(json.dumps({'pairs_completed':len(pair_times),'pairs_total':a.repeats*len(prepared)}),flush=True)
    n=len(pair_rows)
    report={'engine':a.engine,'strategy':strategy,'crop_padding':padding,'crop_sha256':digest.hexdigest(),'repeats':a.repeats,
      'cpu_threads':4,'opencv':cv2.__version__,'init_seconds':init,'numeric_metrics':metrics(rows),
      'dev_metrics':metrics(rows[2:]),'reserved_diagnostic_metrics':metrics(rows[:2]),
      'recognition_mean_seconds':statistics.mean(timings),'recognition_p95_seconds':float(np.percentile(timings,95)),
      'recognition_peak_ram_mb':recognition_rss,'pipeline_extra_init_seconds':pipeline_init,
      'pair_mean_seconds':statistics.mean(pair_times),'pair_p95_seconds':float(np.percentile(pair_times,95)),
      'pipeline_peak_ram_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
      'pipeline_metrics':{'count':n,'exact_match':sum(t==v for t,v,s,d in pair_rows)/n,
         'missing_or_conflicting_rate':sum(not v or s=='conflict' for t,v,s,d in pair_rows)/n,
         'manual_review_rate':sum(d!='VERIFIED' for t,v,s,d in pair_rows)/n,
         'incorrect_but_accepted_rate':sum(t!=v and s=='ok' for t,v,s,d in pair_rows)/n},
      'diagnostics':diagnostics,'pair_diagnostics':pair_diagnostics,
      'model_bytes':(Path(a.weights).stat().st_size if a.weights else
          sum(p.stat().st_size for p in Path(a.paddle_dir).glob('inference.*')) if a.engine!='easyocr' else
          sum((Path(a.easy_dir)/n).stat().st_size for n in ('arabic.pth','craft_mlt_25k.pth'))),
      'limitation':'Only two real identities, previously seen in earlier diagnostics; no independent final real test set.'}
    a.output.write_text(json.dumps(report,indent=2));print(json.dumps({k:report[k] for k in ['engine','numeric_metrics','pipeline_metrics','pair_mean_seconds']}))

if __name__=='__main__':main()
