"""Explicit new-production checks; historical manifests remain read-only compatible."""
from __future__ import annotations

import math
from typing import Any

MAX_CARD_SECONDS = 12.0
PACE = "中速偏快、利落流畅"


def manifest_errors(data: dict[str, Any], *, require: bool = False) -> list[str]:
    if "production_contract_version" not in data:
        return ["new production requires production_contract_version: 1"] if require else []
    errors = []
    if type(data["production_contract_version"]) is not int or data["production_contract_version"] != 1:
        errors.append("production_contract_version must be integer 1")
    voice = data.get("voice")
    instruction = voice.get("instruction", "") if isinstance(voice, dict) else ""
    if not isinstance(instruction, str) or PACE not in instruction:
        errors.append(f"voice.instruction must explicitly include {PACE}")
    for card in data.get("cards", []):
        if not isinstance(card, dict):
            continue
        if card.get("tts_text") and card.get("story_id") != "intro":
            anchor = card.get("visual_anchor")
            if not isinstance(anchor, str) or not anchor.strip():
                errors.append(f"{card.get('id')}: visual_anchor must describe the visible information, not the subtitles")
    return errors


def timing_errors(data: dict[str, Any], timeline: Any) -> list[str]:
    if "production_contract_version" not in data:
        return []
    cards = data.get("cards", [])
    if not isinstance(timeline, list) or len(timeline) != len(cards):
        return ["production timeline must contain every card in manifest order"]
    errors = []
    prior_end = 0.0
    run_image = None
    run_start = 0.0
    for card, row in zip(cards, timeline):
        try:
            if row.get("id") != card.get("id"):
                raise ValueError("card ID/order mismatch")
            start, end, seconds = (float(row[k]) for k in ("start_sec", "end_sec", "duration_sec"))
            if not all(math.isfinite(v) for v in (start, end, seconds)) or seconds <= 0:
                raise ValueError("nonfinite or nonpositive timing")
            if abs(start-prior_end) > 0.002 or abs(end-start-seconds) > 0.002:
                raise ValueError("inconsistent or discontinuous timing")
            if seconds > MAX_CARD_SECONDS + 0.001:
                errors.append(f"{card.get('id')}: card {seconds:.3f}s exceeds 12s; split at a semantic boundary")
            # Changing IDs or subtitle pages cannot disguise a continuously reused static image.
            image = card.get("image")
            static = not card.get("animation") and not card.get("original_clip")
            if not static or image != run_image:
                run_start = start
            if static and image == run_image and end-run_start > MAX_CARD_SECONDS + 0.001:
                errors.append(f"{card.get('id')}: repeated static image held {end-run_start:.3f}s; use a new information view")
            run_image = image if static else None
            prior_end = end
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            errors.append(f"{card.get('id')}: invalid production timeline: {exc}")
    return errors
