"""Evaluation and benchmarking against manually verified ground truth.

    python -m id_ocr.evaluate --data test_data --out outputs/eval_report.json [--cpu] [--augment] [--compare-variants]

Ground truth lives in ``test_data/ground_truth.json`` and is NEVER derived from
the OCR output or from structural validation. Labels whose ``labels_verified``
flag is false are reported as PROVISIONAL, not as measured accuracy.

Metrics are reported separately for the front and back side:
  * exact match (national id, serial, dates) - reported separately from text similarity
  * CER / WER for Arabic text fields (over extracted values, and counting nulls as 100% error)
  * extraction rate, false acceptance (a non-null WRONG value), decision distribution, time
The report stores match flags and numbers only - never the identity values themselves.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import statistics
import sys
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .config import KIND_ATTRIBUTES, OcrConfig
from .engine import EngineError
from .extract import _extract_attributes, _extract_simple
from .image_io import load_image
from .normalize import normalize_unicode
from .pipeline import IdOcrPipeline
from .preprocess import VARIANTS
from .validation import parse_date

EXACT_FIELDS = ["national_id", "serial", "birth_date", "issue_date", "expiry_date"]
DATE_FIELDS = {"birth_date", "issue_date", "expiry_date"}
TEXT_FIELDS = ["first_name", "other_names", "address_line1", "address_line2", "job", "gender", "religion",
               "marital_status"]
SIDE_FIELDS = {
    "front": ["national_id", "serial", "birth_date", "first_name", "other_names", "address_line1", "address_line2"],
    "back": ["national_id", "issue_date", "expiry_date", "job", "gender", "religion", "marital_status"],
}


# ----------------------------------------------------------------------- metrics
def levenshtein(a: Any, b: Any) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    ref, hyp = normalize_unicode(ref), normalize_unicode(hyp)
    return levenshtein(ref, hyp) / max(len(ref), 1)


def wer(ref: str, hyp: str) -> float:
    r, h = normalize_unicode(ref).split(), normalize_unicode(hyp).split()
    return levenshtein(r, h) / max(len(r), 1)


def canon(field: str, value: Optional[str]) -> Optional[str]:
    """Comparison form: dates -> ISO via the same parser; everything else NFKC/whitespace only."""
    if value is None:
        return None
    if field in DATE_FIELDS:
        p = parse_date(value)
        return p.iso() if p.ok else value
    return normalize_unicode(value)


def compare_field(field: str, truth: Optional[str], pred: Optional[str]) -> Dict[str, Any]:
    t, p = canon(field, truth), canon(field, pred)
    row: Dict[str, Any] = {"truth_null": t is None, "pred_null": p is None, "exact": (t == p)}
    row["false_accept"] = p is not None and p != t
    if field in TEXT_FIELDS and t is not None:
        row["cer"] = cer(t, p) if p is not None else None
        row["wer"] = wer(t, p) if p is not None else None
    return row


# ------------------------------------------------------------------ augmentations
def augmentations(img: np.ndarray) -> Dict[str, np.ndarray]:
    """In-memory degradations. Content is unchanged, so the original labels remain valid."""
    h, w = img.shape[:2]
    out: Dict[str, np.ndarray] = {"original": img}
    out["blur_k7"] = cv2.GaussianBlur(img, (7, 7), 0)
    out["low_light"] = np.clip(img.astype(np.float32) * 0.35, 0, 255).astype(np.uint8)
    out["half_res"] = cv2.resize(img, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 3.0, 1.0)
    out["rotate_3deg"] = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 30])
    out["jpeg_q30"] = cv2.imdecode(buf, cv2.IMREAD_COLOR) if ok else img
    return out


# --------------------------------------------------------------------- evaluation
def _load_truth(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def evaluate(data_dir: str, truth_path: str, cfg: OcrConfig, augment: bool = False,
             compare_variants: bool = False) -> Dict[str, Any]:
    truth = _load_truth(truth_path)
    verified = bool(truth.get("_meta", {}).get("labels_verified", False))
    pipe = IdOcrPipeline(cfg)
    pipe.warmup()

    rows: List[Dict[str, Any]] = []
    for sample in truth.get("samples", []):
        for side in ("front", "back"):
            fname, t_side = sample.get(side), sample.get(f"{side}_truth")
            if not fname:
                continue
            base = load_image(os.path.join(data_dir, fname), cfg)
            variants = augmentations(base) if augment else {"original": base}
            for aug_name, img in variants.items():
                res = pipe.process(**{side: img})
                row = {"sample": sample["id"], "side": side, "condition": aug_name,
                       "decision": res.decision.status, "processing_ms": res.public["diagnostics"]["processing_ms"],
                       "fields": {}}
                if t_side is None:
                    row["accuracy"] = "NOT_MEASURABLE_NO_GROUND_TRUTH"
                else:
                    for f in SIDE_FIELDS[side]:
                        if f in t_side:
                            row["fields"][f] = compare_field(f, t_side[f], res.public.get(f))
                rows.append(row)

    report: Dict[str, Any] = {
        "labels_verified": verified,
        "accuracy_basis": "VERIFIED_LABELS" if verified else "PROVISIONAL_UNVERIFIED_LABELS",
        "device": pipe.engine.info().get("readers"),
        "per_side": {s: _summarise([r for r in rows if r["side"] == s]) for s in ("front", "back")},
        "by_condition": {},
        "rows": rows,
    }
    conds = sorted({r["condition"] for r in rows})
    for c in conds:
        report["by_condition"][c] = {s: _summarise([r for r in rows if r["side"] == s and r["condition"] == c])
                                     for s in ("front", "back")}
    if compare_variants:
        report["variant_comparison"] = _compare_variants(truth, data_dir, cfg, pipe)
    return report


def _summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"images": 0}
    out: Dict[str, Any] = {"images": len(rows)}
    dec = [r["decision"] for r in rows]
    out["decision_rates"] = {d: round(dec.count(d) / len(dec), 3) for d in ("VERIFIED", "RETRY", "MANUAL_REVIEW", "REJECTED")}
    times = [r["processing_ms"] for r in rows]
    out["time_ms"] = {"mean": round(statistics.mean(times), 1), "median": round(statistics.median(times), 1),
                      "max": round(max(times), 1)}
    fields: Dict[str, Dict[str, Any]] = {}
    false_accepts_in_verified = 0
    for r in rows:
        for f, m in r["fields"].items():
            d = fields.setdefault(f, {"n": 0, "exact": 0, "extracted": 0, "false_accept": 0, "cer": [], "wer": [], "n_text": 0})
            d["n"] += 1
            d["exact"] += int(m["exact"])
            d["extracted"] += int(not m["pred_null"] and not m["truth_null"])
            d["false_accept"] += int(m["false_accept"])
            if r["decision"] == "VERIFIED" and m["false_accept"]:
                false_accepts_in_verified += 1
            if "cer" in m:
                d["n_text"] += 1
                d["cer"].append(m["cer"] if m["cer"] is not None else 1.0)
                d["wer"].append(m["wer"] if m["wer"] is not None else 1.0)
    summary = {}
    for f, d in fields.items():
        s: Dict[str, Any] = {"n": d["n"], "exact_match": round(d["exact"] / d["n"], 3),
                             "extraction_rate": round(d["extracted"] / d["n"], 3),
                             "false_accepts": d["false_accept"]}
        if d["n_text"]:
            s["cer_nulls_as_100pct"] = round(statistics.mean(d["cer"]), 4)
            s["wer_nulls_as_100pct"] = round(statistics.mean(d["wer"]), 4)
        summary[f] = s
    out["fields"] = summary
    out["false_accepts_inside_VERIFIED_results"] = false_accepts_in_verified
    return out


def _compare_variants(truth: Dict[str, Any], data_dir: str, cfg: OcrConfig, pipe: IdOcrPipeline) -> Dict[str, Any]:
    """One OCR attempt per (field, variant) on the ORIGINAL images - no rereads, no voting."""
    cfg1 = copy.copy(cfg)
    cfg1.max_rereads = 0
    cfg1.full_image_pass = "never"
    table: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for sample in truth.get("samples", []):
        for side in ("front", "back"):
            fname, t_side = sample.get(side), sample.get(f"{side}_truth")
            if not fname or t_side is None:
                continue
            img = load_image(os.path.join(data_dir, fname), cfg)
            for spec in cfg.regions.get(side, []):
                for v in VARIANTS:
                    sp = replace(spec, variants=[v])
                    if spec.kind == KIND_ATTRIBUTES:
                        frs, _ = _extract_attributes(sp, img, pipe.engine, cfg1)
                    else:
                        frs = {spec.name: _extract_simple(sp, img, pipe.engine, cfg1)}
                    for name, fr in frs.items():
                        if name not in t_side:
                            continue
                        m = compare_field(name, t_side[name], fr.value)
                        cell = table.setdefault(f"{side}.{name}", {}).setdefault(v, {"n": 0, "exact": 0, "cer": []})
                        cell["n"] += 1
                        cell["exact"] += int(m["exact"])
                        if "cer" in m:
                            cell["cer"].append(m["cer"] if m["cer"] is not None else 1.0)
    for fld, by_v in table.items():
        for v, cell in by_v.items():
            cell["exact_match"] = round(cell["exact"] / cell["n"], 3)
            cell["cer"] = round(statistics.mean(cell["cer"]), 4) if cell["cer"] else None
            del cell["exact"]
    return table


# ------------------------------------------------------------------------- CLI
def _print_summary(rep: Dict[str, Any]) -> None:
    print(f"Accuracy basis: {rep['accuracy_basis']}   device: {rep['device']}")
    for side, s in rep["per_side"].items():
        print(f"\n== {side.upper()}  images={s.get('images')}")
        if s.get("images", 0) == 0:
            continue
        print(f"   decisions {s['decision_rates']}  time_ms {s['time_ms']}  false_accepts_in_VERIFIED={s['false_accepts_inside_VERIFIED_results']}")
        for f, m in s["fields"].items():
            extra = f"  CER={m['cer_nulls_as_100pct']} WER={m['wer_nulls_as_100pct']}" if "cer_nulls_as_100pct" in m else ""
            print(f"   {f:15s} n={m['n']:3d} exact={m['exact_match']:.3f} extracted={m['extraction_rate']:.3f} false_acc={m['false_accepts']}{extra}")
    if "variant_comparison" in rep:
        print("\n== Variant comparison (single attempt per variant, original images)")
        for fld, by_v in rep["variant_comparison"].items():
            print(f"   {fld:22s} " + "  ".join(f"{v}:exact={c['exact_match']:.2f}" + (f"/cer={c['cer']}" if c['cer'] is not None else "") for v, c in by_v.items()))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="id_ocr.evaluate")
    ap.add_argument("--data", default="test_data")
    ap.add_argument("--truth", default=None, help="default: <data>/ground_truth.json")
    ap.add_argument("--out", default="outputs/eval_report.json")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--model-dir", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--augment", action="store_true", help="also evaluate in-memory degraded copies (blur, dark, ...)")
    ap.add_argument("--compare-variants", action="store_true")
    a = ap.parse_args(argv)
    cfg = OcrConfig(use_gpu=not a.cpu, allow_cpu_fallback=a.cpu, model_dir=a.model_dir, download_models=not a.offline,
                    log_level="WARNING")
    truth_path = a.truth or os.path.join(a.data, "ground_truth.json")
    if not os.path.isfile(truth_path):
        print("No ground_truth.json: accuracy cannot be measured.", file=sys.stderr)
        return 1
    try:
        rep = evaluate(a.data, truth_path, cfg, a.augment, a.compare_variants)
    except EngineError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=2)
    _print_summary(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
