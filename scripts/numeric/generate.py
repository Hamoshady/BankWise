"""Generate labeled SYNTHETIC numeric crops locally; no real identity profiles."""
import argparse, hashlib, json, random
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import yaml


def render(sequence, font_path, rng):
    font=ImageFont.truetype(str(font_path),rng.randint(30,45))
    glyphs='٠١٢٣٤٥٦٧٨٩' if rng.random()<.9 else '0123456789'
    gap=rng.randint(4,10); width=len(sequence)*(font.size+gap)+30;height=80
    bg=rng.randint(200,250);arr=np.full((height,width),bg,np.uint8)
    mask=Image.new('L',(width,height));draw=ImageDraw.Draw(mask);boxes=[];x=15
    for digit in sequence:
        ch=glyphs[int(digit)];b=font.getbbox(ch);glyph=Image.new('L',(width,height));gd=ImageDraw.Draw(glyph)
        gd.text((x,10),ch,font=font,fill=255)
        extent=glyph.getbbox()
        if extent is None:raise ValueError('Font missing digit glyph')
        draw.text((x,10),ch,font=font,fill=255);boxes.append((int(digit),extent))
        x+=max(12,round(font.getlength(ch)))+gap
    width=min(width,x+15);arr=arr[:,:width];m=np.array(mask)[:,:width]
    ink=rng.randint(10,80);arr=(arr*(1-m/255)+ink*m/255).astype(np.uint8)
    # Small geometry changes preserve digit identities; transform box corners too.
    src=np.float32([[0,0],[width-1,0],[width-1,height-1],[0,height-1]])
    dst=src+np.float32([[rng.uniform(-2,2),rng.uniform(-2,2)] for _ in range(4)])
    h=cv2.getPerspectiveTransform(src,dst)
    arr=cv2.warpPerspective(arr,h,(width,height),borderValue=bg)
    labels=[]
    for digit,(x0,y0,x1,y1) in boxes:
        pts=cv2.perspectiveTransform(np.float32([[[x0,y0],[x1,y0],[x1,y1],[x0,y1]]]),h)[0]
        low=np.maximum(pts.min(0),0);high=np.minimum(pts.max(0),[width,height])
        labels.append([digit,float((low[0]+high[0])/2/width),float((low[1]+high[1])/2/height),float((high[0]-low[0])/width),float((high[1]-low[1])/height)])
    if rng.random()<.45:arr=cv2.GaussianBlur(arr,(3,3),rng.uniform(.25,.8))
    noise=np.random.default_rng(rng.randrange(2**32)).normal(0,rng.uniform(0,3),arr.shape)
    arr=np.clip(arr.astype(float)+noise,0,255).astype(np.uint8)
    ok,encoded=cv2.imencode('.jpg',arr,[cv2.IMWRITE_JPEG_QUALITY,rng.randint(65,95)])
    return encoded.tobytes(),labels


def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--train',type=int,default=5000);a=p.parse_args()
    if a.output.exists():p.error('Output exists; refusing overwrite')
    a.output.mkdir(parents=True,mode=0o700)
    fonts=[Path('/usr/share/fonts/truetype/noto')/n for n in ['NotoNaskhArabic-Regular.ttf','NotoNaskhArabic-Bold.ttf','NotoSansArabic-Regular.ttf','NotoSansArabic-Bold.ttf']]
    if not all(f.exists() for f in fonts):p.error('Install licensed Noto Arabic fonts first')
    rng=random.Random(6142);seen=set();manifest=[];font_counts={f.name:0 for f in fonts}
    for split,count in [('train',a.train),('val',400),('test',400)]:
        for folder in ['images','labels']:(a.output/split/folder).mkdir(parents=True)
        for i in range(count):
            while True:
                length=14 if rng.random()<.8 else rng.randint(8,18)
                seq=''.join(str(rng.randrange(10)) for _ in range(length))
                if seq not in seen:seen.add(seq);break
            font=rng.choice(fonts);font_counts[font.name]+=1
            data,boxes=render(seq,font,rng);name=f'{split}_{i:06d}'
            (a.output/split/'images'/f'{name}.jpg').write_bytes(data)
            (a.output/split/'labels'/f'{name}.txt').write_text('\n'.join(' '.join(map(str,b)) for b in boxes)+'\n')
            manifest.append({'image':f'{split}/images/{name}.jpg','sequence':seq,'split':split,'synthetic':True})
    (a.output/'sequences.json').write_text(json.dumps(manifest))
    (a.output/'data.yaml').write_text(yaml.safe_dump({'path':str(a.output.resolve()),'train':'train/images','val':'val/images','test':'test/images','names':[str(i) for i in range(10)]}))
    license_path=Path('/usr/share/doc/fonts-noto-core/copyright')
    (a.output/'FONT_LICENSE.txt').write_text(license_path.read_text())
    (a.output/'provenance.json').write_text(json.dumps({'seed':6142,'synthetic_only':True,'sequence_disjoint':True,'font_license':'SIL OFL 1.1; see FONT_LICENSE.txt','fonts':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in fonts},'counts':{'train':a.train,'val':400,'test':400},'font_counts':font_counts},indent=2))
    print('Synthetic dataset generated; no real identities used.')

if __name__=='__main__':main()
