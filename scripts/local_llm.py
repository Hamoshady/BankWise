"""Manage the pinned CPU llama.cpp service. Model downloads are explicit, never on API startup."""
import argparse,hashlib,subprocess,time
from pathlib import Path
from urllib.request import urlopen

USER_DIR=Path.home()
BASE=USER_DIR/'.local/share/bankwise'
BINARY=BASE/'llama-b12359/llama-b12359/llama-server'
MODEL=BASE/'models/Qwen3.5-4B-Q4_K_M.gguf'
MODEL_SHA256='217002fa8008ef4fc6d548353a3aebef055a96e6ff8e0e2c0953ef8254a89c14'
UNIT=USER_DIR/'.config/systemd/user/bankwise-llama.service'


def ready():
    try:
        with urlopen('http://127.0.0.1:8081/health',timeout=2) as r:return r.status==200
    except Exception:return False


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['up','stop','status','logs','verify']);args=p.parse_args()
    if args.command=='verify':
        with MODEL.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=MODEL_SHA256:raise SystemExit('Model checksum mismatch')
        print('Verified pinned Qwen3.5-4B Q4_K_M SHA256');return
    if args.command in {'stop','status'}:
        raise SystemExit(subprocess.call(['systemctl','--user',args.command,'bankwise-llama.service']))
    if args.command=='logs':
        raise SystemExit(subprocess.call(['journalctl','--user','-u','bankwise-llama.service','-n','30','--no-pager']))
    if not BINARY.is_file() or not MODEL.is_file() or MODEL.stat().st_size!=3032091584:
        raise SystemExit('Pinned runtime or complete model missing; see docs/local_llm.md. No automatic download.')
    config=f'''[Unit]
Description=BankWise local Qwen3.5 CPU inference
After=network.target
[Service]
ExecStart={BINARY} -m {MODEL} --host 127.0.0.1 --port 8081 --alias bankwise-qwen3.5-4b-q4 -c 8192 -np 1 -t 4 -tb 4 -ngl 0 --cache-ram 1024 --reasoning off --jinja --no-webui --log-disable
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
UMask=0077
[Install]
WantedBy=default.target
'''
    changed=not UNIT.exists() or UNIT.read_text()!=config
    UNIT.parent.mkdir(parents=True,exist_ok=True);UNIT.write_text(config)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True)
    subprocess.run(['systemctl','--user','enable','bankwise-llama.service'],check=True)
    subprocess.run(['systemctl','--user','restart' if changed else 'start','bankwise-llama.service'],check=True)
    deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        if ready():print('llama.cpp ready: loopback port 8081, Qwen3.5-4B Q4_K_M, CPU');return
        time.sleep(1)
    raise SystemExit('llama.cpp not ready; run scripts/local_llm.py logs')

if __name__=='__main__':main()
