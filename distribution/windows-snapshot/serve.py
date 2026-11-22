"""Keep llama.cpp loopback-only inside the same container as the API."""
import os,subprocess,time,urllib.request,signal,sys
model='/llm-models/Qwen3.5-4B-Q4_K_M.gguf'
llama=subprocess.Popen(['/opt/llama/llama-server','-m',model,'--host','127.0.0.1','--port','8081','--alias','bankwise-qwen3.5-4b-q4','-c','8192','-np','1','-t','4','-tb','4','-ngl','0','--cache-ram','1024','--reasoning','off','--jinja','--no-webui','--log-disable'])
api=None

def stop(*_):
    for p in (api,llama):
        if p and p.poll() is None:p.terminate()
    for p in (api,llama):
        if p:
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:p.kill()
    sys.exit(0)
for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,stop)
try:
    for _ in range(180):
        if llama.poll() is not None:raise RuntimeError('Local model stopped before readiness')
        try:
            urllib.request.urlopen('http://127.0.0.1:8081/health',timeout=2);break
        except Exception:time.sleep(1)
    else:raise RuntimeError('Local model startup timed out')
    api=subprocess.Popen([sys.executable,'-m','uvicorn','bankwise.api.main:create_app','--factory','--host','0.0.0.0','--port','8000','--workers','1','--no-proxy-headers','--no-access-log'])
    while api.poll() is None and llama.poll() is None:time.sleep(1)
    raise RuntimeError('A required service exited')
finally:
    for p in (api,llama):
        if p and p.poll() is None:p.terminate()
