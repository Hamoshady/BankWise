"""Geometry and recovery rules use synthetic cards, names and dates only."""
from dataclasses import replace

import cv2
import numpy as np
import pytest

from id_ocr.alignment import rectify_card
from id_ocr.config import OcrConfig
from id_ocr.engine import RawRead
from id_ocr.extract import Candidate, FieldResult, _labelled_expiry, _recover_name_line


def test_rectification_removes_background_and_thin_protrusions():
    image = np.zeros((700, 1100, 3), np.uint8)
    cv2.rectangle(image, (150, 100), (950, 600), (230, 230, 230), -1)
    cv2.line(image, (30, 500), (160, 500), (255, 255, 255), 3)
    result, status = rectify_card(image)
    assert status == 'rectified'
    assert abs(result.shape[1] - 800) < 15
    assert abs(result.shape[0] - 500) < 15


def test_rectification_preserves_tight_card_and_unknown_boundary():
    for shape in [(500, 800, 3), (500, 500, 3)]:
        image = np.full(shape, 220, np.uint8)
        result, _ = rectify_card(image)
        assert result is image


class RecoveryEngine:
    def __init__(self, texts, label='حتى'):
        self.texts = iter(texts)
        self.label = label

    def read_layout(self, image):
        return [RawRead(self.label, .95, (350, 70, 430, 125))]

    def read_line(self, image, *args):
        return [RawRead(next(self.texts), .95, (0, 0, image.shape[1], image.shape[0]))]


def expiry_result(texts, original=None, label='حتى'):
    cfg = OcrConfig()
    spec = next(s for s in cfg.regions['back'] if s.name == 'expiry_date')
    original = original or FieldResult('expiry_date', 'back')
    return _labelled_expiry(spec, np.full((700, 1100, 3), 200, np.uint8),
                            RecoveryEngine(texts, label), cfg, original)


@pytest.mark.parametrize('date', ['2030/03/14', '2032/11/29'])
def test_label_recovery_reads_date_without_person_specific_values(date):
    result = expiry_result([date, date])
    assert result.value == date.replace('/', '-')
    assert result.status == 'ok'
    assert result.source == 'expiry_label_localized'


def test_expiry_rejects_partial_and_conflicting_dates():
    assert expiry_result(['2030/03', '2030/03']).value is None
    result = expiry_result(['2030/03/14', '2030/03/15'])
    assert result.value is None and result.status == 'conflict'


def test_expiry_never_overwrites_conflicting_region_value():
    candidate = Candidate('2030/03/13', '2030/03/13', '2030-03-13', .95, 'gray', 'ar')
    original = FieldResult('expiry_date', 'back', value=candidate.value,
                           status='single_read', candidates=[candidate])
    result = expiry_result(['2030/03/14', '2030/03/14'], original)
    assert result.value is None and result.status == 'conflict'


def test_expiry_requires_label():
    assert expiry_result([], label='تاريخ الإصدار').value is None


@pytest.mark.parametrize('name', ['مريم', 'يوسف'])
def test_name_line_recovery_retains_review_flag(name):
    cfg = OcrConfig()
    spec = next(s for s in cfg.regions['front'] if s.name == 'first_name')
    candidates = [Candidate(v, v, v, .9, 'gray', 'ar') for v in (name, 'نص آخر')]
    original = FieldResult('first_name', 'front', status='conflict', candidates=candidates,
                           flags=['candidate_conflict'])
    result = _recover_name_line(spec, np.full((700, 1100, 3), 200, np.uint8),
                                RecoveryEngine([name, name]), cfg, original)
    assert result.value == name and result.status == 'low_confidence'
    assert 'candidate_conflict' in result.flags
    rejected = _recover_name_line(spec, np.full((700, 1100, 3), 200, np.uint8),
                                  RecoveryEngine(['اسم مختلف', 'اسم مختلف']), cfg, original)
    assert rejected is original
