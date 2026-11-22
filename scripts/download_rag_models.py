"""Explicit online provisioning only; serving is always local/offline."""
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
root = Path('/rag-models')
root.mkdir(parents=True, exist_ok=True)
existing = json.loads((root / "manifest.json").read_text()) if (root / "manifest.json").is_file() else {}
manifest = {}
for name, repository in [('embedding', 'intfloat/multilingual-e5-small'), ('llm', 'Qwen/Qwen2.5-1.5B-Instruct')]:
    revision = existing.get(name, {}).get("revision") or HfApi().model_info(repository).sha
    destination = root / name
    snapshot_download(repository, revision=revision, local_dir=destination,
                      allow_patterns=['*.json', '*.safetensors', '*.txt', '*.model', 'README.md', 'LICENSE*'],
                      ignore_patterns=['onnx/*', 'openvino/*'])
    manifest[name] = {'repository': repository, 'revision': revision}
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print('Pinned local RAG models prepared.')
