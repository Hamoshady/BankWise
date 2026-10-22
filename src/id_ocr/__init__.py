"""Local EasyOCR pipeline for Egyptian national ID cards (BankWise AI)."""
from .config import FieldSpec, OcrConfig
from .decision import Decision, DecisionInput, decide
from .engine import EngineError, OcrEngine, get_engine
from .image_io import ImageError
from .merge import merge_sides
from .pipeline import IdOcrPipeline, IdOcrResult, redact_public
from .validation import decode_national_id, parse_date

__all__ = [
    "OcrConfig", "FieldSpec", "IdOcrPipeline", "IdOcrResult", "redact_public", "OcrEngine", "get_engine",
    "EngineError", "ImageError", "Decision", "DecisionInput", "decide", "merge_sides", "decode_national_id",
    "parse_date",
]
