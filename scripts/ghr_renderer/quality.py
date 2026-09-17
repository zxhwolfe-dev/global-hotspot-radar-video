from __future__ import annotations

import math
from typing import Any

from .motion import layer_motion_end


def analyze_motion_plan(data: dict[str, Any], timeline: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    cards: list[dict[str, Any]] = []
    tail = float((data.get("video") or {}).get("tail_hold_seconds", 0.8))
    for index, (card, timing) in enumerate(zip(data.get("cards") or [], timeline)):
        animation = card.get("animation")
        if not isinstance(animation, dict):
            continue
        engine = str(animation.get("engine") or "legacy")
        layer_ends = [layer_motion_end(layer, engine=engine) for layer in animation.get("layers") or []]
        effects = animation.get("effects") or []
        effect_ends = [
            float(effect.get("start_sec", 0.0)) + max(0.0, float(effect.get("duration_sec", 0.0)))
            for effect in effects
            if isinstance(effect, dict)
        ]
        motion_end = max([0.0, *layer_ends, *effect_ends])
        spoken_visible_sec = (
            float(timing.get("spoken_end_sec", timing["end_sec"])) - float(timing["start_sec"])
        )
        stable_read_sec = spoken_visible_sec - motion_end if math.isfinite(motion_end) else float("-inf")
        minimum_stable = max(0.0, float(animation.get("minimum_stable_read_sec", 0.45)))
        card_errors: list[str] = []
        card_warnings: list[str] = []
        if not math.isfinite(motion_end):
            card_warnings.append("legacy continuous drift has no stable end; migrate this card to scene_v2")
        elif stable_read_sec < minimum_stable:
            message = (
                f"motion settles only {stable_read_sec:.3f}s before spoken/source boundary; "
                f"minimum is {minimum_stable:.3f}s"
            )
            (card_errors if engine == "scene_v2" else card_warnings).append(message)
        if index == len(timeline) - 1:
            tail_start = float(timing["duration_sec"]) - tail
            if not math.isfinite(motion_end) or motion_end > tail_start + 1.0 / max(1, int((data.get("video") or {}).get("fps", 30))):
                message = "motion reaches the final 0.8-second tail; the release tail must be visually frozen"
                (card_errors if engine == "scene_v2" else card_warnings).append(message)
        errors.extend(f"{card['id']}: {message}" for message in card_errors)
        warnings.extend(f"{card['id']}: {message}" for message in card_warnings)
        cards.append({
            "card_id": card["id"],
            "engine": engine,
            "motion_end_sec": None if not math.isfinite(motion_end) else round(motion_end, 3),
            "spoken_visible_sec": round(spoken_visible_sec, 3),
            "stable_read_sec": None if not math.isfinite(stable_read_sec) else round(stable_read_sec, 3),
            "minimum_stable_read_sec": minimum_stable,
            "errors": card_errors,
            "warnings": card_warnings,
        })
    return {
        "status": "FAIL" if errors else ("WARN" if warnings else "PASS"),
        "blocking": bool(errors),
        "cards": cards,
        "errors": errors,
        "warnings": warnings,
    }
