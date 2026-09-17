from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
from PIL import Image


@dataclass(frozen=True)
class TransformedLayer:
    pixels: np.ndarray
    anchor_x: float
    anchor_y: float


def load_premultiplied(path: str) -> np.ndarray:
    rgba = np.array(Image.open(path).convert("RGBA"), dtype=np.float32) / 255.0
    rgba[:, :, :3] *= rgba[:, :, 3:4]
    return rgba


def transform_layer(
    source: np.ndarray,
    *,
    target_width: int,
    scale_x: float,
    scale_y: float,
    rotation: float,
    blur: float,
    anchor: tuple[float, float] = (0.5, 0.5),
) -> TransformedLayer:
    source_height, source_width = source.shape[:2]
    resolved_width = max(2, round(target_width * scale_x))
    base_height = source_height * target_width / max(source_width, 1)
    resolved_height = max(2, round(base_height * scale_y))
    layer = cv2.resize(source, (resolved_width, resolved_height), interpolation=cv2.INTER_LANCZOS4)
    anchor_x = min(1.0, max(0.0, float(anchor[0]))) * resolved_width
    anchor_y = min(1.0, max(0.0, float(anchor[1]))) * resolved_height
    if abs(rotation) > 0.01:
        matrix = cv2.getRotationMatrix2D((anchor_x, anchor_y), rotation, 1.0)
        corners = np.array(
            [[0, 0, 1], [resolved_width, 0, 1], [0, resolved_height, 1], [resolved_width, resolved_height, 1]],
            dtype=np.float32,
        )
        transformed_corners = corners @ matrix.T
        minimum = transformed_corners.min(axis=0)
        maximum = transformed_corners.max(axis=0)
        matrix[0, 2] -= minimum[0]
        matrix[1, 2] -= minimum[1]
        layer = cv2.warpAffine(
            layer,
            matrix,
            (max(2, int(np.ceil(maximum[0] - minimum[0]))), max(2, int(np.ceil(maximum[1] - minimum[1])))),
            flags=cv2.INTER_LANCZOS4,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0.0, 0.0, 0.0, 0.0),
        )
        anchor_x, anchor_y = anchor_x - minimum[0], anchor_y - minimum[1]
    if blur > 0.1:
        sigma = min(20.0, float(blur))
        layer = cv2.GaussianBlur(layer, (0, 0), sigmaX=sigma, sigmaY=sigma)
    return TransformedLayer(np.clip(layer, 0.0, 1.0), float(anchor_x), float(anchor_y))


def composite(base: np.ndarray, layer: TransformedLayer, anchor_x: float, anchor_y: float, opacity: float) -> None:
    opacity = min(1.0, max(0.0, float(opacity)))
    if opacity <= 0.001:
        return
    pixels = layer.pixels
    height, width = pixels.shape[:2]
    left, top = round(anchor_x - layer.anchor_x), round(anchor_y - layer.anchor_y)
    right, bottom = left + width, top + height
    crop_left, crop_top = max(0, -left), max(0, -top)
    crop_right = width - max(0, right - base.shape[1])
    crop_bottom = height - max(0, bottom - base.shape[0])
    if crop_left >= crop_right or crop_top >= crop_bottom:
        return
    x1, y1 = max(0, left), max(0, top)
    visible = pixels[crop_top:crop_bottom, crop_left:crop_right]
    alpha = visible[:, :, 3:4] * opacity
    source_rgb = visible[:, :, :3] * opacity
    target = base[y1:y1 + visible.shape[0], x1:x1 + visible.shape[1]].astype(np.float32) / 255.0
    base[y1:y1 + visible.shape[0], x1:x1 + visible.shape[1]] = np.clip(
        (source_rgb + target * (1.0 - alpha)) * 255.0,
        0,
        255,
    ).astype(np.uint8)


def shadow_layer(layer: TransformedLayer, config: dict[str, Any], grounding: float) -> TransformedLayer:
    alpha = layer.pixels[:, :, 3].copy()
    kind = str(config.get("type") or "drop")
    blur = max(0.1, float(config.get("blur", 18.0)))
    if kind == "contact":
        lower = alpha[max(0, int(alpha.shape[0] * 0.48)):]
        if lower.size:
            compressed_height = max(3, round(alpha.shape[0] * (0.10 + 0.08 * (1.0 - grounding))))
            compressed = cv2.resize(lower, (alpha.shape[1], compressed_height), interpolation=cv2.INTER_AREA)
            alpha[:] = 0.0
            alpha[-compressed_height:] = compressed
        blur *= 0.72 + 0.55 * (1.0 - grounding)
    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=blur, sigmaY=blur)
    opacity = min(1.0, max(0.0, float(config.get("opacity", 0.28))))
    alpha = np.clip(alpha * opacity, 0.0, 1.0)
    color = np.array(config.get("color") or [15, 20, 28], dtype=np.float32) / 255.0
    pixels = np.zeros_like(layer.pixels)
    pixels[:, :, 3] = alpha
    pixels[:, :, :3] = color[None, None, :] * alpha[:, :, None]
    return TransformedLayer(pixels, layer.anchor_x, layer.anchor_y)
