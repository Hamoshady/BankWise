"""Render aggregate-only benchmark reports; never read real transcripts or images."""
import argparse,json
from pathlib import Path

NAMES={'easyocr':'EasyOCR','paddle':'PaddleOCR','paddle_numeric':'Paddle numeric (selected original)',
       'yolo_digits':'YOLO11n digits','crnn_ctc':'CRNN + CTC'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--results',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    reports=[json.loads((a.results/f'{key}-final.json').read_text()) for key in NAMES]
    assert len({r['crop_sha256'] for r in reports})==1,'Incomparable crops'
    out=['# Numeric OCR experiment results','',
      '## Decision and scope','',
      'Keep EasyOCR as the production default. Numeric adapters remain disabled. These experiments use only six real crops from three front/back pairs representing two identities; they cannot establish generalization. Both identities were seen in prior diagnostic work. Synthetic test scores must not be interpreted as real-card accuracy.','',
      'Observed outcome: EasyOCR and Paddle each read 4/6 crops exactly; YOLO digits reads 1/6 and CRNN reads 2/6. The specialized models are smaller/faster but do not improve real-card recognition. Paddle has two incorrect raw sequences that pass the shared confidence/format filter; full-pipeline conflict/review checks prevent automatic acceptance in these pairs.','',
      '## Recognition-only comparison','',
      'Hardware: Intel Core i5-12450H; training used an RTX 3050 Laptop GPU (4 GB), while every real-card benchmark used CPU. Initialization timings measure explicit model loading, not interpreter/framework import or model downloading.','',
      'Same aligned template crops and crop SHA256 for all engines; CPU-only inference, four Torch/OMP threads, one OpenCV thread, Docker CPU limit 4 and RAM limit 4 GiB. Three warm repeats per crop (18 timings); initialization and one warmup excluded. Training had finished before timings. Peak RSS includes the interpreter and imported frameworks, not just model allocations.','',
      '| Model | Exact numbers / 6 | Digit CER | Mean / p95 ms | Init s | Peak RAM MiB | Model files MiB |',
      '|---|---:|---:|---:|---:|---:|---:|']
    for r in reports:
        m=r['numeric_metrics'];out.append(f"| {NAMES[r['engine']]} | {round(m['exact_match']*6)}/6 | {m['cer']:.2%} | {1000*r['recognition_mean_seconds']:.1f} / {1000*r['recognition_p95_seconds']:.1f} | {r['init_seconds']:.2f} | {r['recognition_peak_ram_mb']:.1f} | {r['model_bytes']/2**20:.2f} |")
    out+=['','EasyOCR model files include its Arabic recognizer and loaded CRAFT detector, although the numeric call uses recognition only. The later English serial reader is not included in this numeric size. YOLO/CRNN sizes are inference checkpoints. Paddle size includes local inference files. Raw model confidences are not calibrated.','',
      '| Model | Invalid length | Missing digits | Extra digits | Substitutions / GT digit | Incorrect but accepted | Review |',
      '|---|---:|---:|---:|---:|---:|---:|']
    for r in reports:
        m=r['numeric_metrics'];out.append('| '+NAMES[r['engine']]+' | '+' | '.join(f"{m[k]:.2%}" for k in ['invalid_length_rate','missing_digit_rate','extra_digit_rate','substitutions_per_gt_digit','incorrect_but_accepted_rate','manual_review_rate'])+' |')
    out+=['','Incorrect-but-accepted means a wrong raw sequence that passes the common structural validator and confidence threshold without an uncertainty flag. Missing/extra rates count crops with at least one deletion/insertion in a minimum edit alignment. CER uses all characters, not only accepted outputs.','',
      '## Full-pipeline comparison','',
      'Three pairs, three warm repeats each. This includes image handling, all fields and front/back merge. Ordinary EasyOCR/Paddle retain their original retry logic. Numeric experiments use Paddle for other fields, the same template localization, and one numeric read per side; these are not all-field EasyOCR replacement proposals.','',
      '| Model | Final correct NID / 3 | Missing/conflicting | NID incorrect but status ok | Whole-card review | Mean / p95 s | Peak RAM MiB |',
      '|---|---:|---:|---:|---:|---:|---:|']
    for r in reports:
        m=r['pipeline_metrics'];out.append(f"| {NAMES[r['engine']]} | {round(m['exact_match']*3)}/3 | {m['missing_or_conflicting_rate']:.2%} | {m['incorrect_but_accepted_rate']:.2%} | {m['manual_review_rate']:.2%} | {r['pair_mean_seconds']:.2f} / {r['pair_p95_seconds']:.2f} | {r['pipeline_peak_ram_mb']:.1f} |")
    out+=['','A correct final number can still require review. Whole-card review also reflects other fields, expiration and consistency. Experimental single reads retain the existing conservative single_read status.','',
      '## Privacy-safe failure diagnostics','',
      'A is identity 1; B/C are older/newer cards for identity 2. Each cell lists substitutions / missing / extra characters; OK means exact raw sequence. No identity values or images are included.','',
      '| Crop | '+' | '.join(NAMES.values())+' |','|---|'+'---|'*len(NAMES)]
    for i in range(6):
        cells=[]
        for r in reports:
            d=r['diagnostics'][i];e=d['edit_counts'];cells.append('OK' if d['exact_match'] else f"{e['substitutions']} / {e['missing']} / {e['extra']} ({d['raw_length']} chars)")
        out.append('| '+reports[0]['diagnostics'][i]['sample']+' | '+' | '.join(cells)+' |')
    out+=['','Visual inspection found the B.back template crop truncates the last digit at its right edge. Other crops include the sequence but contain faint dotted zeros and varying contrast. Digit substitutions/deletions above are observed errors, not proof of a unique physical cause. No case should be repaired by inferring digits from date/governorate information.','',
      'All engines preserve left-to-right numeric order after normalization; no failed sequence exactly matches the reversed truth. Arabic-Indic normalization is covered by synthetic tests. Raw errors are therefore not explained by a simple reversal or digit-script conversion. Structural validation rejects some outputs; valid-looking substitutions can still be wrong. In the CRNN full pipeline, one of three returned values remains wrong and flagged for review (rather than missing); 2/3 final exact matches must not be read as safe automatic acceptance.','',
      '## Preprocessing selection','',
      'Fifteen configurations (five preprocessing strategies × three horizontal margins) were selected on B/C only: four crops, one identity. The best exact-match setting remained original image, zero extra margin: 3/4 correct, CER 5.36%. Larger margins could lower CER while lowering full-sequence accuracy. Thus crop/preprocessing changes did not reliably fix recognition. A was excluded from this selection but is not an unseen final test.','',
      '## Training','']
    for name,folder in [('CRNN','crnn-training-continued'),('YOLO digits','yolo-training')]:
        data=json.loads((a.results/folder/'summary.json').read_text())
        out+=['### '+name,'','```json',json.dumps(data if name!='CRNN' else {k:v for k,v in data.items() if k!='history'},indent=2),'```','']
    out+=['Both models were trained on synthetic numbers only. Available Thndr annotations are field boxes, not digit boxes or reviewed number transcripts. The synthetic split has unique sequences across train/validation/test, but shares font/renderer families; high synthetic scores do not establish domain transfer. CRNN continued saved weights with a reset optimizer, not an exact training-state resume. YOLO used verified local COCO initialization, not the existing field checkpoint.','',
      '## Tests, integration and next work','',
      'See README.md for architecture, configuration and commands. The final test count is recorded in VALIDATION.md. Numeric models are loaded lazily and reused with inference locks; missing local weights fail clearly. No endpoint/schema/database/frontend changes or production model switch were made. Existing field YOLO checkpoint checksum was verified unchanged.','',
      'Next: collect consented real number crops with independently reviewed sequences and, for YOLO, digit boxes. Split by identity; audit near duplicates. Use separate real development and final test sets. Improve realistic typography/background augmentation using development data only, then fine-tune and rerun the controlled benchmark. Do not select a production model based on these two identities.']
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text('\n'.join(out)+'\n')

if __name__=='__main__':main()
