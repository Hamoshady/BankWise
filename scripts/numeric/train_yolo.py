"""Train digit-only YOLO11n on verified synthetic digit boxes, with separate weights."""
import argparse,json,time
from pathlib import Path
import torch,yaml
from ultralytics import YOLO,settings


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--weights',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--epochs',type=int,default=20);a=p.parse_args()
    if a.output.exists():p.error('Refusing to overwrite experiment')
    if not a.weights.is_file():p.error('Local verified pretrained weights required')
    if not 1<=a.epochs<=40:p.error('epochs must be 1..40')
    cfg=yaml.safe_load(a.data.read_text())
    if cfg['names']!=[str(i) for i in range(10)]:p.error('Expected digit-only labels 0-9')
    # Validate every annotation, not just the class list.
    root=a.data.parent
    for pth in root.glob('*/labels/*.txt'):
        for line in pth.read_text().splitlines():
            values=list(map(float,line.split()))
            if len(values)!=5:raise ValueError('Digit detector requires five-column boxes')
            c,x,y,w,h=values
            if c!=int(c) or not 0<=c<=9 or not(0<=x<=1 and 0<=y<=1 and 0<w<=1 and 0<h<=1):raise ValueError('Invalid digit box')
    a.output.mkdir(parents=True,mode=0o700);torch.set_num_threads(4)
    settings.update({k:False for k in ['sync','wandb','mlflow','clearml','comet','dvc','neptune','raytune','tensorboard','hub'] if k in settings})
    # Bound GPU time: 2000 training images; no sequence overlaps with validation/test.
    paths=sorted((root/'train/images').glob('*.jpg'))[:2000]
    trainlist=a.output/'train-images.txt';trainlist.write_text('\n'.join(str(p.resolve()) for p in paths))
    cfg.update(path=str(root.resolve()),train=str(trainlist.resolve()))
    runtime=a.output/'data.yaml';runtime.write_text(yaml.safe_dump(cfg))
    start=time.perf_counter();model=YOLO(str(a.weights))
    model.train(data=str(runtime),epochs=a.epochs,imgsz=512,batch=16,device=0,workers=0,amp=False,
                project=str(a.output),name='train',seed=731,deterministic=True,freeze=10,patience=5,
                plots=False,verbose=False,cache=False,mosaic=0.,fliplr=0.,flipud=0.,degrees=0.,scale=.1,translate=.02)
    best=YOLO(str(a.output/'train/weights/best.pt'))
    result=best.val(data=str(runtime),split='test',imgsz=512,batch=16,device=0,workers=0,plots=False,save_json=False,
                    project=str(a.output),name='test')
    report={'synthetic_only':True,'train_images':len(paths),'epochs_completed':len((a.output/'train/results.csv').read_text().splitlines())-1,'epochs_requested':a.epochs,'seed':731,
            'training_seconds':time.perf_counter()-start,'digit_detection_test':result.results_dict,'real_generalization_claimed':False}
    (a.output/'summary.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':main()
