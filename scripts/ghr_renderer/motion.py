from __future__ import annotations

import math
import re
from typing import Any


LEGACY_EFFECTS = {"focus_in", "rise_reveal", "slide_left", "stamp", "cursor_tap"}
SCENE_V2_PRESETS = {
    "subject_reveal",
    "evidence_focus",
    "slide_reveal",
    "paper_unfold",
    "stamp_impact",
    "cursor_press",
    "target_response",
    "compare_split",
    "lever_pull",
    "arc_turn",
    "window_lower",
}
EASINGS = {"linear", "smoothstep", "out_cubic", "out_quint", "in_cubic", "in_out_sine", "back_out"}


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def ease(name: str, value: float) -> float:
    value = clamp01(value)
    if name == "linear":
        return value
    if name == "out_cubic":
        return 1.0 - (1.0 - value) ** 3
    if name == "out_quint":
        return 1.0 - (1.0 - value) ** 5
    if name == "in_cubic":
        return value ** 3
    if name == "in_out_sine":
        return -(math.cos(math.pi * value) - 1.0) / 2.0
    if name == "back_out":
        overshoot = 1.70158
        shifted = value - 1.0
        return 1.0 + (overshoot + 1.0) * shifted ** 3 + overshoot * shifted ** 2
    return value * value * (3.0 - 2.0 * value)


def parse_card_time(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace(" ", "")
    if text == "card_start":
        return 0.0
    match = re.fullmatch(r"card_start([+-][0-9]+(?:\.[0-9]+)?)", text)
    if match:
        return float(match.group(1))
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"unsupported card-local time anchor: {value}") from exc


def _first_action(layer: dict[str, Any]) -> dict[str, Any]:
    actions = layer.get("actions")
    if isinstance(actions, list) and actions:
        if not isinstance(actions[0], dict):
            raise ValueError("scene_v2 layer actions must be objects")
        return dict(actions[0])
    effect = str(layer.get("effect") or "focus_in")
    preset = {
        "focus_in": "evidence_focus",
        "rise_reveal": "subject_reveal",
        "slide_left": "slide_reveal",
        "stamp": "stamp_impact",
        "cursor_tap": "cursor_press",
    }.get(effect, effect)
    return {
        "preset": preset,
        "start": layer.get("start_sec", 0.0),
        "duration_sec": layer.get("reveal_sec", 0.72),
        "anticipation_sec": layer.get("anticipation_sec", 0.10),
        "settle_sec": layer.get("settle_sec", 0.28),
        "easing": layer.get("easing", "out_cubic"),
    }


def action_window(layer: dict[str, Any]) -> dict[str, float | str]:
    action = _first_action(layer)
    preset = str(action.get("preset") or "evidence_focus")
    if preset not in SCENE_V2_PRESETS:
        raise ValueError(f"unsupported scene_v2 preset: {preset}")
    easing = str(action.get("easing") or "out_cubic")
    if easing not in EASINGS:
        raise ValueError(f"unsupported scene_v2 easing: {easing}")
    start = max(0.0, parse_card_time(action.get("start"), float(layer.get("start_sec", 0.0))))
    anticipation = max(0.0, float(action.get("anticipation_sec", 0.10)))
    duration = max(0.08, float(action.get("duration_sec", layer.get("reveal_sec", 0.72))))
    settle = max(0.08, float(action.get("settle_sec", 0.28)))
    return {
        "preset": preset,
        "easing": easing,
        "start": start,
        "anticipation": anticipation,
        "duration": duration,
        "settle": settle,
        "action_start": start + anticipation,
        "settle_start": start + anticipation + duration,
        "end": start + anticipation + duration + settle,
    }


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def _phase(window: dict[str, float | str], seconds: float) -> tuple[str, float]:
    start = float(window["start"])
    action_start = float(window["action_start"])
    settle_start = float(window["settle_start"])
    end = float(window["end"])
    if seconds < start:
        return "waiting", 0.0
    if seconds < action_start and action_start > start:
        return "anticipation", clamp01((seconds - start) / (action_start - start))
    if seconds < settle_start:
        return "action", clamp01((seconds - action_start) / max(0.001, settle_start - action_start))
    if seconds < end:
        return "settle", clamp01((seconds - settle_start) / max(0.001, end - settle_start))
    return "hold", 1.0


def scene_v2_state(
    layer: dict[str, Any],
    seconds: float,
    total_sec: float,
    width: int,
    height: int,
) -> dict[str, float | str]:
    window = action_window(layer)
    phase, raw = _phase(window, seconds)
    action = _first_action(layer)
    preset = str(window["preset"])
    action_easing = str(window["easing"])
    position = layer.get("position") or [0.5, 0.5]
    final_x, final_y = float(position[0]) * width, float(position[1]) * height
    final_scale = float(layer.get("to_scale", 1.0))
    from_scale = float(layer.get("from_scale", 0.94 if preset == "evidence_focus" else 0.96))
    final_rotation = float(layer.get("to_rotation", 0.0))
    from_rotation = float(layer.get("from_rotation", 0.0))
    from_blur = max(0.0, float(layer.get("from_blur", 5.0)))
    final_opacity = clamp01(float(layer.get("opacity", 1.0)))
    x, y = final_x, final_y
    scale_x = scale_y = final_scale
    rotation = final_rotation
    opacity = final_opacity
    blur = 0.0
    grounding = 1.0

    if phase == "waiting":
        opacity = 0.0
        grounding = 0.0
        scale_x = scale_y = from_scale
        blur = from_blur
    elif phase == "anticipation":
        p = ease("in_out_sine", raw)
        opacity = final_opacity * 0.12 * p
        scale_x = scale_y = from_scale * _lerp(1.0, 0.985, p)
        rotation = from_rotation
        blur = from_blur
        grounding = 0.0
    elif phase == "action":
        p = ease(action_easing, raw)
        opacity = final_opacity * ease("out_cubic", min(1.0, raw * 1.65))
        blur = from_blur * (1.0 - ease("out_quint", raw))
        grounding = p
        if preset == "subject_reveal":
            distance = float(layer.get("rise_ratio", action.get("rise_ratio", 0.065))) * height
            y = _lerp(final_y + distance, final_y - 0.010 * height, p)
            scale_x = scale_y = _lerp(from_scale, final_scale * 1.012, p)
        elif preset in {"slide_reveal", "compare_split"}:
            direction = -1.0 if str(action.get("direction") or "left") == "right" else 1.0
            distance = float(layer.get("slide_ratio", action.get("slide_ratio", 0.14))) * width * direction
            x = _lerp(final_x + distance, final_x - 0.008 * width * direction, p)
            scale_x = scale_y = _lerp(from_scale, final_scale, p)
        elif preset == "paper_unfold":
            scale_x = _lerp(float(action.get("from_scale_x", 0.96)), final_scale, p)
            scale_y = _lerp(float(action.get("from_scale_y", 0.12)), final_scale * 1.025, p)
        elif preset == "stamp_impact":
            lift = float(action.get("lift_ratio", 0.045)) * height
            y = _lerp(final_y - lift, final_y + 0.008 * height, ease("in_cubic", raw))
            scale_x = scale_y = _lerp(final_scale * 1.10, final_scale * 0.94, ease("in_cubic", raw))
            rotation = _lerp(float(action.get("from_rotation", -6.0)), 1.2, p)
            opacity = final_opacity
        elif preset == "cursor_press":
            origin = action.get("from_position") or layer.get("from_position") or [max(0.0, float(position[0]) - 0.24), min(1.0, float(position[1]) + 0.18)]
            x = _lerp(float(origin[0]) * width, final_x, p)
            y = _lerp(float(origin[1]) * height, final_y, p)
            scale_x = scale_y = final_scale
        elif preset == "target_response":
            scale_x = scale_y = _lerp(final_scale, final_scale * 1.055, math.sin(math.pi * raw))
        elif preset == "lever_pull":
            pull = ease("in_out_sine", raw)
            y = _lerp(final_y - float(action.get("lift_ratio", 0.020)) * height, final_y, pull)
            rotation = _lerp(float(action.get("from_rotation", -16.0)), final_rotation, pull)
            scale_x = scale_y = _lerp(from_scale, final_scale * 1.018, pull)
        elif preset == "arc_turn":
            origin = action.get("from_position") or layer.get("from_position") or [float(position[0]) - 0.28, float(position[1]) + 0.12]
            control = action.get("control_position") or layer.get("control_position") or [float(position[0]) - 0.08, float(position[1]) - 0.10]
            q = 1.0 - p
            x = q * q * float(origin[0]) * width + 2.0 * q * p * float(control[0]) * width + p * p * final_x
            y = q * q * float(origin[1]) * height + 2.0 * q * p * float(control[1]) * height + p * p * final_y
            rotation = _lerp(float(action.get("from_rotation", -12.0)), final_rotation, p)
            scale_x = scale_y = _lerp(from_scale, final_scale, p)
        elif preset == "window_lower":
            distance = float(action.get("drop_ratio", 0.085)) * height
            y = _lerp(final_y - distance, final_y + 0.008 * height, ease("in_out_sine", raw))
            scale_x = scale_y = _lerp(from_scale, final_scale, p)
        else:
            scale_x = scale_y = _lerp(from_scale, final_scale * 1.012, p)
            rotation = _lerp(from_rotation, final_rotation, p)
    elif phase == "settle":
        p = ease("out_cubic", raw)
        if preset == "subject_reveal":
            y = _lerp(final_y - 0.010 * height, final_y, p)
            scale_x = scale_y = _lerp(final_scale * 1.012, final_scale, p)
        elif preset in {"slide_reveal", "compare_split"}:
            direction = -1.0 if str(action.get("direction") or "left") == "right" else 1.0
            x = _lerp(final_x - 0.008 * width * direction, final_x, p)
        elif preset == "paper_unfold":
            scale_y = _lerp(final_scale * 1.025, final_scale, p)
        elif preset == "stamp_impact":
            bounce = ease("back_out", raw)
            y = _lerp(final_y + 0.008 * height, final_y, p)
            scale_x = scale_y = _lerp(final_scale * 0.94, final_scale, bounce)
            rotation = _lerp(1.2, final_rotation, p)
        elif preset == "cursor_press":
            if raw < 0.42:
                press = ease("in_cubic", raw / 0.42)
                scale_x = scale_y = _lerp(final_scale, final_scale * 0.86, press)
            else:
                release = ease("back_out", (raw - 0.42) / 0.58)
                scale_x = scale_y = _lerp(final_scale * 0.86, final_scale, release)
        elif preset == "target_response":
            scale_x = scale_y = _lerp(final_scale * 1.055, final_scale, p)
        elif preset == "lever_pull":
            y = _lerp(final_y + 0.006 * height, final_y, p)
            rotation = _lerp(final_rotation + 1.8, final_rotation, p)
            scale_x = scale_y = _lerp(final_scale * 1.018, final_scale, p)
        elif preset == "arc_turn":
            x = final_x
            y = _lerp(final_y - 0.006 * height, final_y, p)
            rotation = _lerp(final_rotation + 1.2, final_rotation, p)
        elif preset == "window_lower":
            y = _lerp(final_y + 0.008 * height, final_y, p)
            scale_x = scale_y = _lerp(final_scale * 1.008, final_scale, p)
        else:
            scale_x = scale_y = _lerp(final_scale * 1.012, final_scale, p)
        opacity = final_opacity
        blur = 0.0
        grounding = 1.0

    finite_drift = layer.get("finite_drift") or {}
    if isinstance(finite_drift, dict) and float(finite_drift.get("duration_sec", 0.0)) > 0:
        drift_start = max(0.0, parse_card_time(finite_drift.get("start"), float(window["end"])))
        drift_duration = float(finite_drift["duration_sec"])
        drift_p = ease("in_out_sine", clamp01((seconds - drift_start) / drift_duration))
        offset = finite_drift.get("offset") or [0.0, 0.0]
        x += float(offset[0]) * width * drift_p
        y += float(offset[1]) * height * drift_p

    shake = layer.get("shake") or {}
    shake_start = float(shake.get("start_sec", -1.0))
    shake_end = float(shake.get("end_sec", -1.0))
    if shake_start <= seconds <= shake_end and shake_end > shake_start:
        local = (seconds - shake_start) / (shake_end - shake_start)
        amplitude = float(shake.get("amplitude_px", 5.0)) * math.sin(math.pi * local)
        frequency = float(shake.get("frequency_hz", 13.0))
        x += amplitude * math.sin(seconds * frequency * math.tau)
        y += amplitude * 0.55 * math.sin(seconds * frequency * 0.83 * math.tau + 0.7)

    return {
        "x": x,
        "y": y,
        "scale": (scale_x + scale_y) / 2.0,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "rotation": rotation,
        "opacity": opacity,
        "blur": blur,
        "grounding": grounding,
        "phase": phase,
        "motion_end_sec": float(window["end"]),
        "stable": 1.0 if phase == "hold" else 0.0,
    }


def layer_motion_end(layer: dict[str, Any], *, engine: str) -> float:
    if engine == "scene_v2":
        end = float(action_window(layer)["end"])
        finite_drift = layer.get("finite_drift") or {}
        if isinstance(finite_drift, dict) and float(finite_drift.get("duration_sec", 0.0)) > 0:
            start = parse_card_time(finite_drift.get("start"), end)
            end = max(end, start + float(finite_drift["duration_sec"]))
    else:
        end = float(layer.get("start_sec", 0.0)) + max(0.08, float(layer.get("reveal_sec", 0.55)))
        if isinstance(layer.get("drift"), (list, tuple)) and any(float(item) for item in layer["drift"]):
            return math.inf
    for name in ("shake", "pulse"):
        effect = layer.get(name) or {}
        if not isinstance(effect, dict):
            continue
        start = float(effect.get("start_sec", -1.0))
        if name == "shake":
            effect_end = float(effect.get("end_sec", -1.0))
        else:
            effect_end = start + float(effect.get("duration_sec", 0.0))
        end = max(end, effect_end)
    return end
