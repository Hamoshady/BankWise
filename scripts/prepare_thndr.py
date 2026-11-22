"""Prepare a private experimental Thndr export; no automatic production approval.

Keep one representative per Roboflow source name. Cluster low-frequency perceptual
hash near duplicates across ALL splits before deterministic split assignment.
This controls image-level leakage, not unobservable identity-level leakage.
"""
import argparse, hashlib, json, math, shutil
from collections import defaultdict, Counter
from pathlib import Path
import cv2
import numpy as np
import yaml

MAPPING = {'Add1':'front.address_line1','Add2':'front.address_line2',
 'ExpDate':'back.expiry_date','First_Name':'front.first_name','Gender':'back.gender',
 'ID':'front.national_id','IssueDate':'back.issue_date','Job1':'back.job',
 'Job2':'back.job_line2','Last_Name':'front.other_names','Religion':'back.religion',
 'Serial_Num':'front.serial','Status':'back.marital_status','HusbandName':'back.husband_name'}


def box(values):
    if not values or not math.isfinite(values[0]) or values[0] != int(values[0]) or values[0] < 0:
        raise ValueError('Invalid class index')
    if len(values)==5:
        c,x,y,w,h=values
        bounds=(x-w/2,y-h/2,x+w/2,y+h/2)
    elif len(values)>=7 and len(values)%2==1:
        pts=np.array(values[1:]).reshape(-1,2)
        bounds=(*pts.min(axis=0),*pts.max(axis=0))
    else: raise ValueError('Invalid annotation length')
    if not all(math.isfinite(v) for v in values):raise ValueError('Nonfinite annotation')
    x0,y0,x1,y1=bounds
    if not (0<=x0<x1<=1 and 0<=y0<y1<=1):raise ValueError('Invalid bounds')
    return int(values[0]),((x0+x1)/2,(y0+y1)/2,x1-x0,y1-y0)


def prepare(source, output):
    source,output=Path(source),Path(output)
    if output.exists():raise ValueError('Output must not already exist')
    cfg=yaml.safe_load((source/'data.yaml').read_text());names=cfg['names']
    groups=defaultdict(list)
    for p in sorted(source.glob('*/images/*.jpg')):groups[p.stem.split('.rf.')[0]].append(p)
    representatives=[];hashes=[]
    for group,paths in sorted(groups.items()):
        # Middle-brightness variant avoids selecting the darkest/brightest generated image.
        ranked=sorted(paths,key=lambda p:float(cv2.imread(str(p),0).mean()))
        p=ranked[len(ranked)//2];representatives.append(p)
        gray=cv2.resize(cv2.imread(str(p),0),(32,32)).astype(np.float32)
        d=cv2.dct(gray)[:8,:8].flatten()[1:];bits=d>np.median(d)
        hashes.append(sum(int(v)<<i for i,v in enumerate(bits)))
    parent=list(range(len(hashes)))
    def find(a):
        while parent[a]!=a:parent[a]=parent[parent[a]];a=parent[a]
        return a
    for a in range(len(hashes)):
        for b in range(a):
            if (hashes[a]^hashes[b]).bit_count()<=8:parent[find(a)]=find(b)
    clusters=defaultdict(list)
    for i,p in enumerate(representatives):clusters[find(i)].append(p)
    mapping_names=list(MAPPING.values());counts=Counter();classcounts=defaultdict(Counter);polygons=0
    output.mkdir(parents=True,mode=0o700)
    for cluster,paths in clusters.items():
        key=min(p.stem.split('.rf.')[0] for p in paths)
        value=int(hashlib.sha256(('thndr-v1:'+key).encode()).hexdigest()[:8],16)%100
        split='test' if value<15 else 'val' if value<30 else 'train'
        for p in paths:
            lines=[]
            for line in (p.parent.parent/'labels'/(p.stem+'.txt')).read_text().splitlines():
                vals=list(map(float,line.split()));cls,coords=box(vals);name=names[cls]
                if name not in MAPPING:continue # sparsely/inconsistently labeled full-card Front/Back
                polygons+=len(vals)!=5
                new= mapping_names.index(MAPPING[name]);lines.append(str(new)+' '+' '.join(f'{v:.8f}' for v in coords))
                classcounts[split][MAPPING[name]]+=1
            ident=hashlib.sha256(p.name.encode()).hexdigest()[:20]
            for folder in ['images','labels']:(output/split/folder).mkdir(parents=True,exist_ok=True)
            shutil.copyfile(p,output/split/'images'/f'{ident}.jpg')
            (output/split/'labels'/f'{ident}.txt').write_text('\n'.join(lines)+'\n');counts[split]+=1
    (output/'data.yaml').write_text(yaml.safe_dump({'path':str(output.resolve()),'train':'train/images','val':'val/images','test':'test/images','names':mapping_names}))
    for p in source.glob('README*.txt'):shutil.copyfile(p,output/p.name)
    report={'source_images':sum(map(len,groups.values())),'source_name_groups':len(groups),
      'perceptual_clusters':len(clusters),'split_counts':counts,'class_counts':classcounts,'converted_polygons':polygons,
      'dropped_classes':['Front','Back'],'identity_separation_verified':False,
      'limitations':['Perceptual grouping does not prove different identities across splits.',
                     'Printed birth date and back national-ID are not labeled.',
                     'Export already stretched to 512x512; original aspect ratios unavailable.']}
    (output/'preparation.json').write_text(json.dumps(report,indent=2));return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');a=p.parse_args()
    print(json.dumps(prepare(a.source,a.output),indent=2))
