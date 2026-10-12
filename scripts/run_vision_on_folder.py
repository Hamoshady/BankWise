import sys
from pathlib import Path

import cv2

sys.path.insert(0, "src")
from bankwise.core.config import load_identity_config
from bankwise.core.errors import InvalidImageError
from bankwise.identity.vision.processor import process_card
from bankwise.identity.vision.schemas import CardSide


def main(folder: str, output_folder: str) -> None:
    cfg = load_identity_config().vision
    out = Path(output_folder)
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(folder).iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        try:
            output = process_card(path.read_bytes(), CardSide.FRONT, cfg)
        except InvalidImageError as exc:
            print(path.name, "invalid", str(exc))
            continue
        result = output.result
        print(path.name, result.status.value, result.reasons, result.detection.confidence)
        if output.card_image is not None:
            cv2.imwrite(str(out / f"{path.stem}_card.jpg"), output.card_image)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])