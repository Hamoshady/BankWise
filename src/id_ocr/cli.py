"""Command line interface.

    python -m id_ocr.cli check-env [--verify-gpu] [--cpu]
    python -m id_ocr.cli run --front front.jpg [--back back.jpg] [--cpu] [--show-pii] [--out result.json]

By default the printed JSON is REDACTED (masked id, hidden names/addresses) so
terminal output and screenshots do not leak identity data. Use --show-pii to
print real values, and --out to write the full JSON (you own its retention).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from .config import OcrConfig
from .engine import EngineError, get_engine
from .pipeline import IdOcrPipeline


def _cfg(a: argparse.Namespace) -> OcrConfig:
    cfg = OcrConfig(use_gpu=not a.cpu, allow_cpu_fallback=a.cpu, model_dir=a.model_dir,
                    download_models=not a.offline, debug_dir=getattr(a, "debug_dir", None))
    if getattr(a, "regions", None):
        cfg.load_regions(a.regions)
    if getattr(a, "full_pass", None):
        cfg.full_image_pass = a.full_pass
    return cfg


def cmd_check_env(a: argparse.Namespace) -> int:
    cfg = _cfg(a)
    eng = get_engine(cfg)
    try:
        eng.reader("ar")
    except EngineError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": str(exc), "env": eng.info()}, indent=2))
        return 2
    out = {"ok": True, "env": eng.info()}
    if a.verify_gpu:
        out["gpu_verification"] = eng.verify_gpu_usage()
    print(json.dumps(out, indent=2))
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    cfg = _cfg(a)
    try:
        res = IdOcrPipeline(cfg).process(front=a.front, back=a.back, front_rotate=a.front_rotate,
                                         back_rotate=a.back_rotate, attempt=a.attempt)
    except EngineError as exc:
        print(json.dumps({"error": exc.code, "message": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(res.to_json(redact=not a.show_pii))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(res.to_json(redact=False))
    return 0 if res.decision.status != "REJECTED" else 1


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="id_ocr")
    p.add_argument("--cpu", action="store_true", help="explicitly run on CPU (recovery option)")
    p.add_argument("--model-dir", default=None)
    p.add_argument("--offline", action="store_true", help="never download model weights")
    p.add_argument("--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check-env")
    c.add_argument("--verify-gpu", action="store_true")
    c.set_defaults(fn=cmd_check_env)

    r = sub.add_parser("run")
    r.add_argument("--front")
    r.add_argument("--back")
    r.add_argument("--front-rotate", type=int, default=0)
    r.add_argument("--back-rotate", type=int, default=0)
    r.add_argument("--regions", help="JSON file overriding region definitions")
    r.add_argument("--debug-dir", help="save region crops here (contain personal data)")
    r.add_argument("--full-pass", choices=["never", "fallback", "always"])
    r.add_argument("--attempt", type=int, default=1)
    r.add_argument("--show-pii", action="store_true")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_run)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
