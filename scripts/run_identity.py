import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
from bankwise.core.config import load_identity_config
from bankwise.identity.reader.vlm_reader import VlmFieldReader
from bankwise.identity.service import verify_identity_fields


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("front")
    parser.add_argument("back")
    parser.add_argument("--show-pii", action="store_true")
    args = parser.parse_args()
    cfg = load_identity_config()
    reader = VlmFieldReader(cfg.reader)
    report = verify_identity_fields(
        Path(args.front).read_bytes(), Path(args.back).read_bytes(), reader, cfg
    )
    if args.show_pii:
        print(report.model_dump_json(indent=2))
    else:
        print(json.dumps(report.redacted(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()