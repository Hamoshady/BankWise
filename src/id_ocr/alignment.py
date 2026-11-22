"""Conservative card boundary rectification before applying normalized field regions.

No OCR values or person-specific templates participate in geometry estimation. If a
large, convex card-shaped contour is not found, preserve the original image.
"""
from __future__ import annotations
import cv2
import numpy as np


def _ordered(points):
    p = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = p.mean(axis=0)
    p = p[np.argsort(np.arctan2(p[:, 1]-center[1], p[:, 0]-center[0]))]
    p = np.roll(p, -np.argmin(p.sum(axis=1)), axis=0)
    return p


def rectify_card(image):
    """Return (image, status). Only bounded landscape quadrilaterals are accepted."""
    height, width = image.shape[:2]
    scale = min(1.0, 1000.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    _, bright = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    masks = [cv2.morphologyEx(bright, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8)),
             cv2.morphologyEx(cv2.Canny(gray, 40, 120), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))]
    area = small.shape[0] * small.shape[1]
    candidates = []
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            if cv2.contourArea(contour) < .35 * area: continue
            perimeter = cv2.arcLength(contour, True)
            poly = cv2.approxPolyDP(contour, .02*perimeter, True)
            if len(poly) != 4 or not cv2.isContourConvex(poly): continue
            p = _ordered(poly)
            top, right, bottom, left = [np.linalg.norm(p[(i+1)%4]-p[i]) for i in range(4)]
            if min(top, right, bottom, left) < 50: continue
            ratio = (top+bottom)/(right+left)
            if not 1.35 <= ratio <= 1.85: continue
            if min(top,bottom)/max(top,bottom) < .78 or min(left,right)/max(left,right) < .78: continue
            corners = []
            for i in range(4):
                a,b = p[(i-1)%4]-p[i],p[(i+1)%4]-p[i]
                corners.append(abs(np.dot(a,b)/(np.linalg.norm(a)*np.linalg.norm(b))))
            if max(corners) > .30: continue
            coverage=cv2.contourArea(p)/area
            if coverage < .35: continue
            candidates.append((coverage,p))
        if candidates:
            break  # clean card silhouette first; edges only when thresholding found no card
    if not candidates: return image, 'boundary_not_found'
    coverage, p = max(candidates,key=lambda item:item[0])
    if coverage > .88:
        return image, "already_aligned"
    p = p / scale
    # Do not resample a tightly cropped, already aligned image merely to trim rounded corners.
    bounding = np.array([[0,0],[width-1,0],[width-1,height-1],[0,height-1]],dtype=np.float32)
    if np.max(np.linalg.norm(p-bounding,axis=1)) < .035*min(width,height):
        return image,'already_aligned'
    w = int(round((np.linalg.norm(p[1]-p[0])+np.linalg.norm(p[2]-p[3]))/2))
    h = int(round((np.linalg.norm(p[3]-p[0])+np.linalg.norm(p[2]-p[1]))/2))
    target=np.array([[0,0],[w-1,0],[w-1,h-1],[0,h-1]],dtype=np.float32)
    matrix=cv2.getPerspectiveTransform(p,target)
    return cv2.warpPerspective(image,matrix,(w,h),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_REPLICATE),'rectified'
