"""Install the pinned x86-64 Linux runtime during image BUILD only."""
import hashlib,json,tarfile,urllib.request,shutil,tempfile
from pathlib import Path
m=json.loads(Path('/tmp/llm-manifest.json').read_text())
url=f"https://github.com/ggml-org/llama.cpp/releases/download/{m['runtime_release']}/{m['runtime_archive']}"
with tempfile.TemporaryDirectory() as d:
    archive=Path(d)/'runtime.tar.gz'
    with urllib.request.urlopen(url,timeout=120) as src,archive.open('wb') as dst:shutil.copyfileobj(src,dst)
    with archive.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    if sha!=m['runtime_archive_sha256']:raise RuntimeError('Runtime checksum mismatch')
    with tarfile.open(archive) as t:t.extractall(Path(d)/'unpacked',filter='data')
    binary=next((Path(d)/'unpacked').rglob('llama-server'))
    shutil.copytree(binary.parent,'/opt/llama',symlinks=True)
