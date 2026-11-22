"""Bounded local experimental YOLO11n training, never changes the serving backend."""
import argparse, json, time
from pathlib import Path
import torch, yaml
from ultralytics import YOLO, settings


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--weights',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--epochs',type=int,default=25);p.add_argument('--threads',type=int,default=4)
    p.add_argument('--device',choices=['cpu','0'],default='cpu')
    a=p.parse_args()
    if not 1<=a.epochs<=50: p.error('epochs must be 1..50')
    if not a.weights.is_file():p.error('local pretrained weights required')
    torch.set_num_threads(a.threads)
    settings.update({k:False for k in ['sync','wandb','mlflow','clearml','comet','dvc','neptune','raytune','tensorboard','hub'] if k in settings})
    a.output.mkdir(parents=True,exist_ok=True)
    cfg=yaml.safe_load(a.data.read_text());cfg['path']=str(a.data.parent.resolve())
    runtime=a.output/'data.yaml';runtime.write_text(yaml.safe_dump(cfg))
    start=time.perf_counter();model=YOLO(str(a.weights))
    model.train(data=str(runtime),epochs=a.epochs,imgsz=512,batch=8,device=a.device,workers=0,amp=False,
                project=str(a.output),name='train',exist_ok=False,seed=42,deterministic=True,
                freeze=10,patience=8,plots=False,verbose=False,cache=False,
                mosaic=.3,close_mosaic=5,fliplr=0.,flipud=0.,degrees=10.,scale=.2,translate=.05)
    best=a.output/'train/weights/best.pt'
    trained=YOLO(str(best))
    result=trained.val(data=str(runtime),split='test',imgsz=512,batch=8,device=a.device,workers=0,
                       plots=False,save_json=False,project=str(a.output),name='test')
    report={'epochs_requested':a.epochs,'backbone_layers_frozen':10,'device':a.device,
            'elapsed_seconds':time.perf_counter()-start,'test_metrics':result.results_dict,
            'classes':trained.names,'identity_split_verified':False,
            'serving_default_changed':False}
    (a.output/'summary.json').write_text(json.dumps(report,indent=2))

if __name__=='__main__':main()
