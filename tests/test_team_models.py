"""Synthetic packaging tests: no personal data or actual weights."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile
import pytest

spec=importlib.util.spec_from_file_location('team_models',Path(__file__).parents[1]/'scripts/team_models.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture(root):
    entries=[]
    for name,data in [('llm/test.gguf',b'0123456789'*20),('ocr/test.pth',b'synthetic weights')]:
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        entries.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
    return {'files':entries}


def test_roundtrip_split_and_verify(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'CHUNK',64)
    source=tmp_path/'source'; manifest=fixture(source); assets=tmp_path/'assets'
    m.pack(source,assets,manifest)
    assert len(list(assets.glob('*.part*')))==4
    target=tmp_path/'target';m.install(assets,target,manifest);m.verify(target,manifest)


def test_corrupt_asset_rejected_before_install(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'CHUNK',64)
    source=tmp_path/'source';manifest=fixture(source);assets=tmp_path/'assets'
    m.pack(source,assets,manifest)
    next(assets.glob('*.part*')).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='release asset'):m.install(assets,tmp_path/'target',manifest)
    assert not (tmp_path/'target').exists()


def test_missing_model(tmp_path):
    manifest=fixture(tmp_path);(tmp_path/'ocr/test.pth').unlink()
    with pytest.raises(ValueError,match='Missing or corrupt'):m.verify(tmp_path,manifest)


@pytest.mark.parametrize('path',['../outside','/outside','.'])
def test_path_escape(tmp_path,path):
    with pytest.raises(ValueError,match='Unsafe'):m.checked(tmp_path,path)


def test_untrusted_asset_manifest_cannot_replace_pinned_weights(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'CHUNK',64)
    src=tmp_path/'source';manifest=fixture(src);assets=tmp_path/'assets';m.pack(src,assets,manifest)
    with zipfile.ZipFile(assets/'models-core.zip','w') as z:z.writestr('ocr/test.pth',b'bad replacement!!')
    a=json.loads((assets/'assets.json').read_text())
    for item in a['assets']:
        p=assets/item['name'];item.update(bytes=p.stat().st_size,sha256=m.digest(p))
    (assets/'assets.json').write_text(json.dumps(a))
    with pytest.raises(ValueError):m.install(assets,tmp_path/'target',manifest)
