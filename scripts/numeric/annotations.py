"""Create blank local annotation manifests or audit human-reviewed numeric labels.

Does not derive labels from OCR. Output must be stored outside the repository.
"""
import argparse
import json
from pathlib import Path


def audit(rows):
    groups={};seen_images={}
    for row in rows:
        sequence=row.get('sequence')
        if row.get('reviewed') is not True or not isinstance(sequence,str) or not sequence.isascii() or not sequence.isdigit():
            raise ValueError('Each annotation needs a human-reviewed digit sequence')
        split=row.get('split');identity=row.get('identity_group')
        if split not in ('train','val','test') or not identity:
            raise ValueError('Explicit split and pseudonymous identity group required')
        if identity in groups and groups[identity]!=split:
            raise ValueError('Identity crosses dataset splits')
        groups[identity]=split
        path=Path(row['image']).resolve()
        if not path.is_file():raise ValueError('Image missing')
        import hashlib
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen_images and seen_images[digest]!=split:
            raise ValueError('Exact duplicate crosses splits')
        seen_images[digest]=split
        boxes=row.get('digit_boxes')
        if boxes is not None:
            import math
            if len(boxes)!=len(sequence):raise ValueError('Digit annotation count mismatch')
            for box in boxes:
                if len(box)!=5 or not all(math.isfinite(v) for v in box):raise ValueError('Invalid box')
                x0,y0,x1,y1,digit=box
                if not(0<=x0<x1<=1 and 0<=y0<y1<=1 and digit==int(digit) and 0<=digit<=9):
                    raise ValueError('Invalid normalized digit box')
            ordered=sorted(boxes,key=lambda b:(b[0]+b[2])/2)
            if ''.join(str(int(b[4])) for b in ordered)!=sequence:
                raise ValueError('Digit boxes disagree with reviewed sequence')
    return {'images':len(rows),'identity_groups':len(groups),'exact_duplicate_check':True,
            'near_duplicate_visual_review_required':True,'identity_groups_not_independently_verified':True}


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    blank=sub.add_parser('blank');blank.add_argument('--images',type=Path,required=True);blank.add_argument('--output',type=Path,required=True)
    check=sub.add_parser('audit');check.add_argument('manifest',type=Path)
    a=p.parse_args()
    if a.command=='blank':
        repo=Path(__file__).resolve().parents[2]
        if a.output.resolve().is_relative_to(repo):p.error('Sensitive annotations must stay outside the repository')
        rows=[{'image':str(f.resolve()),'sequence':None,'digit_boxes':None,'identity_group':None,'split':None,'reviewed':False}
              for f in sorted(a.images.iterdir()) if f.suffix.lower() in ('.png','.jpg','.jpeg')]
        with a.output.open('x') as stream:json.dump(rows,stream,indent=2)
        a.output.chmod(0o600);print(json.dumps({'blank_annotations':len(rows)}))
    else:print(json.dumps(audit(json.loads(a.manifest.read_text()))))

if __name__=='__main__':main()
