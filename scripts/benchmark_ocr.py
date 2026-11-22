"""Offline benchmark: private manifest in, aggregate metrics out (no OCR values).

Run each backend in a fresh process on the same hardware. Manifest:
{"samples":[{"id":"A","front":"/private/front.jpg","back":"/private/back.jpg",
 "truth":{"first_name":"...","national_id":"..."},
 "crop_truth":{"front.first_name":"...","back.expiry_date":"..."}}]}
Truth null means absent. Omitted truth fields are NOT scored. Keep real manifests outside git.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time

import cv2
import numpy as np

from id_ocr import IdOcrPipeline, OcrConfig
from id_ocr.alignment import rectify_card
from id_ocr.image_io import crop_normalized, load_image
from id_ocr.normalize import normalize_unicode, to_western_digits


def normalized(value):
    return to_western_digits(normalize_unicode(value or ''))


def distance(a, b):
    previous = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j]+1, current[-1]+1, previous[j-1]+(x != y)))
        previous = current
    return previous[-1]


def score(pairs):
    if not pairs:
        return {'count': 0, 'exact_match': None, 'cer': None}
    chars = sum(len(normalized(expected)) for expected, _ in pairs)
    return {'count': len(pairs),
            'exact_match': sum(normalized(a) == normalized(b) for a, b in pairs)/len(pairs),
            'cer': sum(distance(normalized(a), normalized(b)) for a, b in pairs)/chars if chars else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--backend', choices=['easyocr','paddle'], required=True)
    parser.add_argument('--model-dir', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--yolo-weights', help='Optional trusted local custom field detector weights')
    parser.add_argument('--yolo-always', action='store_true', help='Experimental localization on every supplied side')
    parser.add_argument('--yolo-stretch-square', action='store_true', help='Match Thndr 512x512 stretch preprocessing')
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or not 1 <= args.threads <= 16:
        parser.error('repeats must be 1..10 and threads 1..16')
    cv2.setNumThreads(1)
    runtime_start = time.perf_counter()
    if args.backend == 'easyocr' or args.yolo_weights:
        import torch
        torch.set_num_threads(args.threads)
    samples = json.loads(args.manifest.read_text())['samples']
    cfg = OcrConfig(backend=args.backend, model_dir=args.model_dir if args.backend == 'easyocr' else None,
                   paddle_model_dir=args.model_dir if args.backend == 'paddle' else None,
                   cpu_threads=args.threads, use_gpu=False, download_models=False, log_level='ERROR',
                   yolo_weights=args.yolo_weights, yolo_always=args.yolo_always,
                   yolo_stretch_square=args.yolo_stretch_square)
    start = time.perf_counter()
    pipeline = IdOcrPipeline(cfg)
    pipeline.warmup()
    init_seconds = time.perf_counter()-runtime_start
    crop_pairs = defaultdict(list)
    crop_latencies = []
    digest = hashlib.sha256()
    prepared = []
    # Exactly the same alignment, coordinates, original pixels and read_line path for both engines.
    for sample in samples:
        inputs = {side: load_image(sample[side], cfg) for side in ('front','back')}
        prepared.append((sample, inputs))
        for side, image in inputs.items():
            aligned, _ = rectify_card(image)
            for spec in cfg.regions[side]:
                key = side+'.'+spec.name
                if key not in sample.get('crop_truth', {}):
                    continue
                crop = crop_normalized(aligned, spec.box)
                digest.update(crop.tobytes())
                start = time.perf_counter()
                reads = pipeline.engine.read_line(crop, 'en' if spec.kind == 'serial' else 'ar')
                crop_latencies.append(time.perf_counter()-start)
                crop_pairs[key].append((sample['crop_truth'][key], ' '.join(r.text for r in reads)))
    # Warm complete pipeline once; excluded from steady-state latency measurements.
    warm_start = time.perf_counter()
    pipeline.process(**prepared[0][1])
    warmup_seconds = time.perf_counter()-warm_start
    latencies = []
    fields = defaultdict(list)
    cards = []
    review = 0
    for repetition in range(args.repeats):
        for sample, inputs in prepared:
            start = time.perf_counter()
            result = pipeline.process(**inputs)
            latencies.append(time.perf_counter()-start)
            print(json.dumps({"completed_runs": len(latencies), "total_runs": args.repeats*len(samples)}), flush=True)
            if repetition == 0:
                matched, missing, incorrect, uncertain = [], [], [], []
                for field, expected in sample.get('truth', {}).items():
                    value = result.public.get(field)
                    fields[field].append((expected, value))
                    if normalized(expected) == normalized(value): matched.append(field)
                    elif not value: missing.append(field)
                    else: incorrect.append(field)
                    if result.public['validation']['field_status'].get(field) != 'ok': uncertain.append(field)
                needs_review = result.decision.status != 'VERIFIED'
                review += needs_review
                cards.append({'sample': sample['id'], 'matched': matched, 'missing': missing,
                              'incorrect': incorrect, 'uncertain': uncertain, 'manual_review': needs_review,
                              'field_diagnostics': {side: {name: {'status': f.status,
                                  'flags': f.flags, 'confidence': f.confidence,
                                  'attempts': len(f.candidates)} for name, f in data.fields.items()}
                                  for side, data in result.sides.items()},
                              'complete_correct': not missing and not incorrect and bool(sample.get('truth'))})
    all_pairs = [pair for pairs in fields.values() for pair in pairs]
    all_crops = [pair for pairs in crop_pairs.values() for pair in pairs]
    report = {'backend': args.backend, 'model': pipeline.engine.info(), 'cpu_threads': args.threads,
              'torch_threads_after_inference': sys.modules['torch'].get_num_threads() if 'torch' in sys.modules else None,
              'yolo_enabled': bool(args.yolo_weights),
              'yolo_always': args.yolo_always,
              'yolo_stretch_square': args.yolo_stretch_square,
              'opencv_version': cv2.__version__, 'numpy_version': np.__version__, 'sample_count': len(samples),
              'normalization': 'Unicode NFKC, whitespace/bidi cleanup, Western digits; NO Arabic letter substitution',
              'initialization_seconds': init_seconds, 'pipeline_warmup_seconds': warmup_seconds,
              'warm_mean_seconds': statistics.mean(latencies), 'warm_p95_seconds': float(np.percentile(latencies,95)),
              'warm_measurements': len(latencies), 'peak_process_rss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
              'field_metrics': {k:score(v) for k,v in fields.items()}, 'overall_fields':score(all_pairs),
              'complete_card_accuracy':sum(c['complete_correct'] for c in cards)/len(cards),
              'manual_review_rate': review/len(samples), 'cards': cards,
              'same_crop_comparison': {'pixel_digest':digest.hexdigest(), 'metrics':score(all_crops),
                   'fields':{k:score(v) for k,v in crop_pairs.items()},
                   'total_seconds':sum(crop_latencies), 'crop_count':len(crop_latencies)},
              'note':'Small paired-image benchmark, not population accuracy. RSS includes Python/libraries; no remote OCR.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k:report[k] for k in ('backend','sample_count','warm_mean_seconds','warm_p95_seconds','overall_fields','complete_card_accuracy','manual_review_rate','peak_process_rss_mb')}))


if __name__ == '__main__':
    main()
