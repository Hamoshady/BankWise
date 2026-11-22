"""Initialize a new private output volume from the publisher's snapshot, once."""
from pathlib import Path
import os,tarfile,subprocess,sys
out=Path('/app/outputs');marker=out/'.snapshot-imported'
if not marker.exists():
    if any(p.is_file() or p.is_symlink() for p in out.rglob('*')):raise RuntimeError('Output volume is nonempty; refusing to replace it')
    with tarfile.open('/snapshot/outputs.tar') as archive:archive.extractall(out,filter='data')
    for base,dirs,files in os.walk(out):
        os.chown(base,10001,10001)
        for name in files:os.chown(Path(base)/name,10001,10001)
    marker.write_text('Snapshot imported once. Do not delete.\n');os.chown(marker,10001,10001)
os.setgid(10001);os.setuid(10001)
subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True)
print('Database and private output snapshot ready.')
