"""Restore output files into a NEW volume once, then apply normal migrations."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

out=Path('/app/outputs');marker=out/'.team-snapshot-imported'
if not marker.exists():
    if any(p.is_file() or p.is_symlink() for p in out.rglob('*')):
        raise RuntimeError('Nonempty output volume: refusing to replace existing data')
    shutil.copytree('/snapshot/runtime_outputs',out,dirs_exist_ok=True)
    marker.write_text('Imported once; do not remove.\n')
for base,dirs,files in os.walk(out):
    os.chown(base,10001,10001)
    for name in files:os.chown(Path(base)/name,10001,10001)
os.setgid(10001);os.setuid(10001)
subprocess.run([sys.executable,'-m','alembic','upgrade','head'],check=True)
