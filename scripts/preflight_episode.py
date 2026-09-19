#!/usr/bin/env python3
"""Read-only episode preflight. No TTS, network, rendering or input rewriting.

Run after assets/manifest preparation and before the first render; run again
for revisions. This is a deterministic subset, not a replacement for the
renderer, editorial review, media decoding or listening to the final audio.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from check_selection_research import check as check_research
from ghr_renderer.production_contracts import manifest_errors
from ghr_renderer.contracts import safe_resolve_asset
from ghr_renderer.subtitle_validation import expected_pages


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def check_project(root: Path, *, require_research: bool = False, require_production: bool = False) -> dict[str, Any]:
    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}
    hashes: dict[str, str] = {}

    def result() -> dict[str, Any]:
        return {"status": "FAIL" if errors else "STRUCTURE_OK", "root": str(root),
                "scope": "manifest_captions_asset_paths_and_research_records",
                "publication_readiness": "not_assessed", "errors": errors,
                "warnings": warnings, "checks": checks, "input_sha256": hashes}

    def load(name: str) -> dict[str, Any]:
        path = safe_resolve_asset(root, name, label=name)
        raw = path.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError(f"{name} must contain a JSON object")
        return value

    def asset(value: Any, label: str) -> None:
        try:
            path = safe_resolve_asset(root, value, label=label)
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"{label}: missing or empty asset: {value!r}")
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"{label}: {exc}")

    try:
        content = load("content_manifest.json")
        errors.extend(manifest_errors(content, require=require_production))
        video = content.get("video")
        if not isinstance(video, dict):
            raise ValueError("content_manifest.video must be an object")
        version_value = content.get("quality_contract_version", 1)
        if isinstance(version_value, bool) or str(version_value) not in {"1", "2", "3"}:
            raise ValueError("quality_contract_version must be 1, 2 or 3")
        version = int(version_value)
        tail = float(video.get("tail_hold_seconds", -1))
        if not math.isfinite(tail) or abs(tail - 0.8) > 0.001:
            errors.append("video.tail_hold_seconds must be finite and exactly 0.8")
        cards = content.get("cards")
        if not isinstance(cards, list) or len(cards) < 2 or any(not isinstance(card, dict) for card in cards):
            raise ValueError("cards must contain a cover and spoken card objects")
        ids = [str(card.get("id") or "").strip() for card in cards]
        if not all(ids) or len(set(ids)) != len(ids):
            errors.append("cards need unique non-empty IDs")
        for i, card in enumerate(cards):
            speech = str(card.get("tts_text") or "").strip()
            if (i == 0 and speech) or (i > 0 and not speech):
                errors.append(f"cards[{i}]: only the first card may be silent")
            asset(card.get("image"), f"cards[{i}].image")
            animation = card.get("animation")
            if animation:
                if not isinstance(animation, dict) or not isinstance(animation.get("layers"), list):
                    errors.append(f"cards[{i}].animation needs an object with a layers list")
                else:
                    for j, layer in enumerate(animation["layers"]):
                        if not isinstance(layer, dict):
                            errors.append(f"cards[{i}].animation.layers[{j}] must be an object")
                        else:
                            asset(layer.get("asset"), f"cards[{i}].animation.layers[{j}]")
            clip = card.get("original_clip")
            if clip:
                if not isinstance(clip, dict):
                    errors.append(f"cards[{i}].original_clip must be an object")
                else:
                    for key in ("video", "audio"):
                        asset(clip.get(key), f"cards[{i}].original_clip.{key}")
        if video.get("publish_cover_asset"):
            asset(video["publish_cover_asset"], "video.publish_cover_asset")
        pages, caption_errors = expected_pages(content)
        errors.extend(caption_errors)
        checks["captions"] = {"expected_page_count": len(pages), "errors": caption_errors}
        selection_path = root / "selection_report.json"
        selection = load("selection_report.json") if selection_path.exists() or selection_path.is_symlink() else {}
        if version >= 2 and not selection:
            errors.append("quality contract v2/v3 requires selection_report.json")
        if version >= 2 and selection:
            chosen = selection.get("final_selection")
            required = ("topic", "today_delta", "audience_payoff", "primary_evidence", "visual_proof", "unknowns", "editorial_angle")
            if not isinstance(chosen, list) or not chosen:
                errors.append("selection_report.final_selection must be a non-empty list")
            else:
                for i, row in enumerate(chosen):
                    if not isinstance(row, dict) or any(row.get(key) in (None, "", []) for key in required):
                        errors.append(f"final_selection[{i}] is missing existing selection fields")
        # Presence (even null/invalid) opts into strict validation. No saved PASS is trusted.
        configured = "research_contract_version" in selection
        if configured or require_research:
            sources = load("source_manifest.json")
            research = check_research(selection, sources, require=True)
        else:
            research = {"status": "NOT_CONFIGURED", "errors": [],
                        "warnings": ["legacy research records not checked; use --require-research for new episodes"]}
        errors.extend(research["errors"])
        warnings.extend(research["warnings"])
        checks["research"] = research
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RuntimeError) as exc:
        errors.append(f"preflight input error: {type(exc).__name__}: {exc}")
    return result()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_root", type=Path)
    parser.add_argument("--require-research", action="store_true", help="Require the P0 research contract for new episodes")
    parser.add_argument("--require-production", action="store_true", help="Require current voice and visual production contract")
    args = parser.parse_args()
    report = check_project(args.episode_root, require_research=args.require_research, require_production=args.require_production)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
