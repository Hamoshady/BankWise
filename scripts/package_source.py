"""Create a source-only share archive with an explicit file allowlist.

Never packages local databases, images, secrets, virtualenvs, model weights or Git
history. Model/binary distribution is a separate workflow.
"""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT_FILES = {'.env.example','.gitignore','.dockerignore','README.md','SHARE.md',
              'pyproject.toml','alembic.ini','compose.yaml'}
ROOTS = {'src','ui','configs','knowledge','docs','tests','scripts','evaluation','distribution'}
EXTENSIONS = {'.py','.css','.json','.md','.yaml','.yml','.txt','.toml','.ini','.mako','.ps1','.bat'}


def selected(root):
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.is_symlink():continue
        rel=path.relative_to(root)
        if any(part.startswith('.') or part=='__pycache__' for part in rel.parts):
            if str(rel) not in ROOT_FILES:continue
        if len(rel.parts)==1:
            if rel.name not in ROOT_FILES and not rel.name.startswith(('requirements','Dockerfile')):continue
        elif rel.parts[0] not in ROOTS or path.suffix.lower() not in EXTENSIONS:
            continue
        yield path,rel


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];output=a.output.resolve()
    if output.is_relative_to(root):p.error('Write the archive outside the project')
    if output.exists():p.error('Refusing to overwrite an existing archive')
    output.parent.mkdir(parents=True,exist_ok=True)
    entries=list(selected(root));manifest=[]
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for path,rel in entries:
            data=path.read_bytes();name='bankwise_ai/'+rel.as_posix()
            z.writestr(name,data)
            manifest.append(hashlib.sha256(data).hexdigest()+'  '+rel.as_posix())
        z.writestr('bankwise_ai/SOURCE_MANIFEST.sha256','\n'.join(manifest)+'\n')
    with zipfile.ZipFile(output) as z:
        if z.testzip() is not None:raise RuntimeError('Archive integrity failed')
    print(f'Source files: {len(entries)}; ZIP bytes: {output.stat().st_size}; output: {output}')

if __name__=='__main__':main()
