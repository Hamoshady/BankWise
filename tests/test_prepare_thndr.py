import importlib.util
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('prepare',Path(__file__).parents[1]/'scripts/prepare_thndr.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_polygon_conversion_preserves_extent():
 c,b=m.box([3,.1,.2,.7,.2,.7,.6,.1,.6])
 assert c==3
 assert b==pytest.approx((.4,.4,.6,.4))

@pytest.mark.parametrize('row',[[0,float('nan'),.5,.1,.1],[0,.9,.5,.8,.1],[0,.1,.2]])
def test_invalid_geometry_rejected(row):
 with pytest.raises(ValueError):m.box(row)
