"""Read-only audit of a LOCAL extracted YOLO detection export; no training/upload.

The report omits image names and pixels. Exact duplicate detection does not prove
identity-level separation: inspect near-duplicates and group identities manually.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

from PIL import Image
import yaml


def audit(root):
    root = Path(root).resolve()
    cfg = yaml.safe_load((root / 'data.yaml').read_text())
    raw = cfg['names']
    names = dict(enumerate(raw)) if isinstance(raw, list) else {int(k): v for k, v in raw.items()}
    counts, issues, sizes = Counter(), Counter(), Counter()
    hashes = defaultdict(list)
    splits = Counter()
    # Roboflow exports use split/images and split/labels; fail rather than guess.
    for split in ('train', 'valid', 'val', 'test'):
        folder = root / split / 'images'
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.webp'}:
                continue
            splits[split] += 1
            try:
                with Image.open(path) as im:
                    im.load()
                    rgb = im.convert('RGB')
                    sizes[f'{im.width}x{im.height}'] += 1
                    digest = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
                    hashes[digest].append(split)
            except (OSError, ValueError):
                issues['invalid_images'] += 1
            label = root / split / 'labels' / (path.stem + '.txt')
            if not label.is_file():
                issues['missing_label_files'] += 1
                continue
            lines = label.read_text().splitlines()
            if not lines:
                issues['empty_label_files'] += 1
            for line in lines:
                try:
                    fields = line.split()
                    if len(fields) != 5:
                        if len(fields) >= 7 and len(fields) % 2 == 1:
                            issues['non_box_polygon_rows'] += 1
                            continue
                        raise ValueError()
                    cls = int(fields[0])
                    x, y, w, h = map(float, fields[1:])
                    if cls not in names or not all(math.isfinite(v) for v in (x,y,w,h)):
                        raise ValueError()
                    if not (0 < w <= 1 and 0 < h <= 1 and x-w/2 >= -1e-6 and
                            y-h/2 >= -1e-6 and x+w/2 <= 1+1e-6 and y+h/2 <= 1+1e-6):
                        raise ValueError()
                    counts[names[cls]] += 1
                except (ValueError, OverflowError):
                    issues['invalid_boxes'] += 1
    if not splits:
        raise ValueError('No supported split/images directories; inspect the export layout.')
    return {'classes': names, 'images_by_split': splits, 'boxes_by_class': counts,
            'image_dimensions': sizes, 'issues': issues,
            'exact_duplicate_groups': sum(len(v)>1 for v in hashes.values()),
            'cross_split_exact_duplicate_groups': sum(len(set(v))>1 for v in hashes.values()),
            'training_approved': False,
            'remaining_checks': ['actual field box overlays', 'license/provenance files',
                                 'near-duplicates and identity grouping', 'independent test identities',
                                 'class mapping including front/back ID and multi-line occupation']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.dataset)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n')
    print('Audit report written. Manual review is still required; no training was started.')


if __name__ == '__main__':
    main()
