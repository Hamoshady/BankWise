import cv2
import numpy as np


def order_points(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32).reshape(4, 2)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).reshape(4)
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(sums)]
    ordered[2] = pts[np.argmax(sums)]
    ordered[1] = pts[np.argmin(diffs)]
    ordered[3] = pts[np.argmax(diffs)]
    return ordered


def quad_dimensions(ordered: np.ndarray) -> tuple[float, float]:
    top = np.linalg.norm(ordered[1] - ordered[0])
    bottom = np.linalg.norm(ordered[2] - ordered[3])
    left = np.linalg.norm(ordered[3] - ordered[0])
    right = np.linalg.norm(ordered[2] - ordered[1])
    return float((top + bottom) / 2.0), float((left + right) / 2.0)


def warp_card(
    image: np.ndarray, corners: np.ndarray, out_width: int, out_height: int
) -> np.ndarray:
    ordered = order_points(corners)
    width, height = quad_dimensions(ordered)
    if height > width:
        ordered = np.roll(ordered, -1, axis=0)
    destination = np.array(
        [[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    return cv2.warpPerspective(
        image,
        matrix,
        (out_width, out_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )