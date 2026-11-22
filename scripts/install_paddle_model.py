"""Explicit one-time download of official PaddlePaddle Arabic weights, never called by the API."""
import argparse
import hashlib
from pathlib import Path
import tempfile
import urllib.request

BASE = "https://huggingface.co/PaddlePaddle/arabic_PP-OCRv5_mobile_rec/resolve/33d91636a65dca87f5562cc48860332ae367ee1b/"
FILES = {'inference.json': 'd43832804d89c0e18d59f8446a3babd0ef60c44bc335efddac3176caf71d50af', 'inference.pdiparams': '4b4271fd1dd89a40b2056e1a42e58de7c0df2fdcf1a97ca9a8916d7ec45f9143', 'inference.yml': '21368419e6c016c31db55d316d59e11c128e1913e6e6fe10287084710043d3a6'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    for name, expected in FILES.items():
        target = args.directory / name
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            continue
        with urllib.request.urlopen(BASE + name, timeout=120) as response:
            data = response.read(16*1024*1024 + 1)
        if len(data) > 16*1024*1024 or hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit('Model download checksum/size mismatch; no unverified file installed.')
        with tempfile.NamedTemporaryFile(dir=args.directory, delete=False) as stream:
            temp = Path(stream.name)
            try:
                stream.write(data)
                stream.flush()
                temp.replace(target)
            finally:
                temp.unlink(missing_ok=True)
    print('Official Arabic recognition model installed and checksums verified.')


if __name__ == '__main__':
    main()
