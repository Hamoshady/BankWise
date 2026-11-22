import importlib.util
from pathlib import Path
from PIL import Image

spec = importlib.util.spec_from_file_location('audit_dataset', Path(__file__).parents[1]/'scripts/audit_yolo_dataset.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_audit_detects_leakage_and_invalid_boxes_without_modifying_data(tmp_path):
    (tmp_path/'data.yaml').write_text('names: [First_Name]\n')
    for split in ('train', 'test'):
        images = tmp_path/split/'images'; images.mkdir(parents=True)
        labels = tmp_path/split/'labels'; labels.mkdir()
        Image.new('RGB',(32,24),'white').save(images/'synthetic.png')
        (labels/'synthetic.txt').write_text('0 0.5 0.5 0.2 0.2\n0 0.9 0.5 0.8 0.2\n0 nan 0.5 0.2 0.2\n')
    result = module.audit(tmp_path)
    assert result['cross_split_exact_duplicate_groups'] == 1
    assert result['boxes_by_class']['First_Name'] == 2
    assert result['issues']['invalid_boxes'] == 4
    assert result['training_approved'] is False
