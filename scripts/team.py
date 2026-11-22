"""One-command source checkout launcher: Python 3.11+, Docker, and GitHub CLI."""
import argparse
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from team_models import ROOT, MANIFEST, verify, download, install


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['start','stop','status','logs'])
    p.add_argument('--port',type=int,default=8501)
    a=p.parse_args()
    if not shutil.which('docker'):raise SystemExit('Install and start Docker Desktop (Linux containers) or Docker Engine with Compose.')
    env=ROOT/'.team.env'
    if a.command=='start':
        if not 1024<=a.port<=65535:raise SystemExit('Choose a port between 1024 and 65535.')
        subprocess.run(['docker','info','--format','{{.OSType}}/{{.Architecture}}'],check=True)
        info=subprocess.check_output(['docker','info','--format','{{.OSType}}/{{.Architecture}}'],text=True).strip()
        if info not in ('linux/x86_64','linux/amd64'):
            raise SystemExit('This bundle requires x86-64 Linux containers (Windows WSL2 is supported).')
        m=json.loads(MANIFEST.read_text());models=ROOT/'models';assets=ROOT/'model_downloads'
        try:verify(models,m)
        except ValueError:
            if not (assets/'assets.json').is_file():download(assets,m)
            install(assets,models,m)
        if not env.exists():env.write_text(f'TEAM_DB_PASSWORD={secrets.token_hex(24)}\nTEAM_UI_PORT={a.port}\n')
        else:
            lines=[x for x in env.read_text().splitlines() if not x.startswith('TEAM_UI_PORT=')]
            env.write_text('\n'.join(lines)+f'\nTEAM_UI_PORT={a.port}\n')
    elif not env.exists():raise SystemExit('Run python scripts/team.py start first.')
    cmd=['docker','compose','--env-file',str(env),'-f',str(ROOT/'compose.team.yaml')]
    if a.command=='start':
        subprocess.run(cmd+['build'],cwd=ROOT,check=True)
        subprocess.run(cmd+['up','-d','--wait','--wait-timeout','1200'],cwd=ROOT,check=True)
        print(f'BankWise ready: http://127.0.0.1:{a.port}/')
    else:
        subprocess.run(cmd+[{'stop':'stop','status':'ps','logs':'logs'}[a.command]]+(['--tail','100'] if a.command=='logs' else []),cwd=ROOT,check=True)

if __name__=='__main__':main()
