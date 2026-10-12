import os
from functools import lru_cache
from pathlib import Path
from typing import List, Literal, Tuple

import yaml
from pydantic import BaseModel, ValidationError

from bankwise.core.errors import ConfigError

Region = Tuple[float, float, float, float]


class VisionConfig(BaseModel):
    max_image_bytes: int
    max_pixels: int
    max_working_side: int
    min_width: int
    min_height: int
    sharpness_reference_width: int
    min_sharpness: float
    min_brightness: float
    max_brightness: float
    max_dark_ratio: float
    max_bright_ratio: float
    max_glare_ratio: float
    card_aspect_ratio: float
    aspect_tolerance: float
    min_card_area_ratio: float
    min_detection_confidence: float
    canonical_width: int
    canonical_height: int
    detection_side: int


class ReaderConfig(BaseModel):
    provider: Literal["ollama", "gemini", "anthropic", "groq"]
    model: str
    allow_cloud: bool
    api_key_env: str
    base_url: str
    timeout_seconds: float
    max_retries: int
    retry_initial_delay: float
    retry_max_delay: float
    max_image_side: int
    jpeg_quality: int
    zoom_scale: float
    min_agreeing_reads: int
    front_zoom_regions: List[Region]
    front_text_regions: List[Region]
    back_zoom_regions: List[Region]
    back_text_regions: List[Region]
    collect_sensitive_fields: bool = True



class IdentityConfig(BaseModel):
    vision: VisionConfig
    reader: ReaderConfig


def default_config_path() -> Path:
    env_value = os.environ.get("BANKWISE_IDENTITY_CONFIG")
    if env_value:
        return Path(env_value)
    return Path(__file__).resolve().parents[3] / "configs" / "identity.yaml"


@lru_cache(maxsize=1)
def load_identity_config(path: str | None = None) -> IdentityConfig:
    config_path = Path(path) if path else default_config_path()
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        return IdentityConfig(**raw)
    except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
        raise ConfigError(f"invalid identity config: {config_path}") from exc
