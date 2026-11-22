"""Publish/install hash-verified local model assets; no inference-time downloads."""
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'configs/team_models.json'
CHUNK = 1024 * 1024 * 1024


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def checked(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError('Unsafe model path')
    return path


def valid(path, item):
    return path.is_file() and path.stat().st_size == item['bytes'] and digest(path) == item['sha256']


def verify(root, manifest):
    for item in manifest['files']:
        if not valid(checked(root, item['path']), item):
            raise ValueError('Missing or corrupt model: ' + item['path'])


def acquire(root, manifest):
    """CI fetches exact public upstream bytes; project checkpoints come from Git."""
    root.mkdir(parents=True, exist_ok=True)
    for item in manifest['files']:
        path = checked(root, item['path'])
        if valid(path, item):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.download')
        if item.get('seed'):
            shutil.copyfile(checked(ROOT, item['seed']), temp)
        else:
            # curl handles transient upstream failures without adding Python dependencies.
            subprocess.run(['curl', '--fail', '--location', '--retry', '5', '--retry-delay', '3',
                            '--connect-timeout', '30', '--max-time', '1800',
                            '--output', str(temp), item['url']], check=True)
            if item.get('zip_member'):
                with zipfile.ZipFile(temp) as archive, archive.open(item['zip_member']) as src:
                    with path.with_suffix('.extracted').open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                temp.unlink()
                path.with_suffix('.extracted').replace(temp)
        if not valid(temp, item):
            temp.unlink(missing_ok=True)
            raise ValueError('Upstream checksum mismatch: ' + item['path'])
        temp.replace(path)
    verify(root, manifest)


def pack(root, output, manifest):
    verify(root, manifest)
    output.mkdir(parents=True, exist_ok=True)
    # Standard ZIP for non-LLM models; Qwen is split for the release asset size limit.
    with zipfile.ZipFile(output/'models-core.zip', 'w', zipfile.ZIP_STORED) as archive:
        for item in manifest['files']:
            if not item['path'].startswith('llm/'):
                archive.write(root/item['path'], item['path'])
    parts = []
    for item in manifest['files']:
        if not item['path'].startswith('llm/'):
            continue
        names = []
        with (root/item['path']).open('rb') as src:
            index = 1
            while block := src.read(CHUNK):
                name = Path(item['path']).name + f'.part{index:02}'
                (output/name).write_bytes(block)
                names.append(name)
                index += 1
        parts.append({'path':item['path'], 'parts':names})
    assets = [{'name':p.name,'bytes':p.stat().st_size,'sha256':digest(p)}
              for p in sorted(output.iterdir()) if p.is_file() and p.name != 'assets.json']
    (output/'assets.json').write_text(json.dumps({'assets':assets,'parts':parts},indent=2)+'\n')


def install(source, root, manifest):
    asset_manifest = json.loads((source/'assets.json').read_text())
    for item in asset_manifest['assets']:
        if not valid(checked(source,item['name']), item):
            raise ValueError('Missing or corrupt release asset: '+item['name'])
    root.mkdir(parents=True, exist_ok=True)
    expected = {x['path']:x for x in manifest['files']}
    with zipfile.ZipFile(source/'models-core.zip') as archive:
        for info in archive.infolist():
            if info.filename not in expected or info.file_size != expected[info.filename]['bytes']:
                raise ValueError('Unexpected archive member')
            target = checked(root,info.filename)
            target.parent.mkdir(parents=True,exist_ok=True)
            temp = target.with_name(target.name+'.installing')
            with archive.open(info) as src,temp.open('wb') as dst:
                shutil.copyfileobj(src,dst)
            if not valid(temp, expected[info.filename]):
                temp.unlink();raise ValueError('Model checksum mismatch')
            temp.replace(target)
    for group in asset_manifest['parts']:
        target = checked(root,group['path'])
        if group['path'] not in expected:
            raise ValueError('Unexpected split model')
        target.parent.mkdir(parents=True,exist_ok=True)
        temp = target.with_name(target.name+'.installing')
        with temp.open('wb') as dst:
            for name in group['parts']:
                with checked(source,name).open('rb') as src:
                    shutil.copyfileobj(src,dst)
        if not valid(temp, expected[group['path']]):
            temp.unlink();raise ValueError('Reassembled model checksum mismatch')
        temp.replace(target)
    verify(root,manifest)
    print('All model files verified and installed. EasyOCR remains the default.')


def download(output, manifest):
    output.mkdir(parents=True,exist_ok=True)
    # gh uses the user's own login for private repositories; tokens never enter Git.
    if not shutil.which('gh'):
        raise SystemExit('Install GitHub CLI and run gh auth login, or download all release assets into model_downloads manually.')
    subprocess.run(['gh','release','download',manifest['release'],'--repo',manifest['repository'],
                    '--dir',str(output),'--skip-existing'],check=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['acquire','pack','install','verify','download'])
    p.add_argument('--models',type=Path,default=ROOT/'models')
    p.add_argument('--assets',type=Path,default=ROOT/'model_downloads')
    a=p.parse_args(); m=json.loads(MANIFEST.read_text())
    if a.command=='acquire':acquire(a.models,m)
    elif a.command=='pack':pack(a.models,a.assets,m)
    elif a.command=='install':install(a.assets,a.models,m)
    elif a.command=='verify':verify(a.models,m);print('All model checksums verified.')
    else:download(a.assets,m)

if __name__=='__main__':main()
