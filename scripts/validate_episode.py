#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from scan_visual_risks import scan_episode

EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920
EXPECTED_FPS = 30.0
EXPECTED_TAIL = 0.8
TAIL_MIN = 0.72
TAIL_MAX = 0.90

PRIVATE_COPY_PATTERNS = (
    r"关键边界",
    r"画面由\s*imagen",
    r"ai\s*工作站.*生图",
    r"情绪标签",
    r"自定义音色",
    r"未使用收费",
    r"收费生成视频",
    r"generated\s+by\s+imagen",
    r"voice\s+tags?",
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def probe(path: Path) -> dict[str, Any]:
    result = run(
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_rate,channels",
        "-of", "json", str(path),
    )
    return json.loads(result.stdout)


def fps_value(rate: str) -> float:
    numerator, denominator = rate.split("/", 1)
    return float(numerator) / float(denominator)


def trailing_silence(path: Path, duration_sec: float) -> float | None:
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a:0",
            "-af", "silencedetect=n=-45dB:d=0.3", "-f", "null", "-",
        ],
        check=False, capture_output=True, text=True,
    )
    starts = [float(value) for value in re.findall(r"silence_start:\s*([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([0-9.]+)", result.stderr)]
    if not starts or not ends:
        return None
    end = ends[-1]
    if end < duration_sec - 0.12:
        return None
    return end - starts[-1]


def validate(root: Path) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, Any] = {}

    required = {
        "content_manifest": root / "content_manifest.json",
        "episode_manifest": root / "episode_manifest.json",
        "source_manifest": root / "source_manifest.json",
        "content_audit": root / "final" / "content_audit.json",
        "publish_info": root / "publish_info.md",
        "subtitles": root / "final" / "subtitles.srt",
    }
    for name, path in required.items():
        if not path.is_file():
            errors.append(f"missing {name}: {path}")

    if errors:
        return {"status": "FAIL", "root": str(root), "errors": errors, "warnings": warnings, "checks": checks}

    content = read_json(required["content_manifest"])
    quality_contract_version = int(content.get("quality_contract_version", 1)) if isinstance(content, dict) else 1
    if quality_contract_version >= 2:
        selection_report = root / "selection_report.json"
        if not selection_report.is_file():
            errors.append("quality contract v2 requires selection_report.json")
        else:
            selection = read_json(selection_report)
            final_selection = selection.get("final_selection") if isinstance(selection, dict) else None
            if not isinstance(final_selection, list) or not final_selection:
                errors.append("selection_report.json needs a non-empty final_selection list")
            else:
                required_selection_fields = (
                    "topic", "today_delta", "audience_payoff", "primary_evidence",
                    "visual_proof", "unknowns", "editorial_angle",
                )
                for index, item in enumerate(final_selection, 1):
                    missing = [field for field in required_selection_fields if not isinstance(item, dict) or item.get(field) in (None, "", [])]
                    if missing:
                        errors.append(f"selection_report final_selection[{index}] is incomplete: {missing}")
    episode = read_json(required["episode_manifest"])
    sources = read_json(required["source_manifest"])
    content_audit = read_json(required["content_audit"])
    if content_audit.get("status") != "PASS":
        errors.append("final/content_audit.json must record status PASS")
    language = str(content.get("language") or "").lower()
    if quality_contract_version >= 3:
        required_audit_sections = [
            "facts_and_sources", "safety_rights_privacy", "platform_policy",
            "editorial_risk", "editorial_quality",
        ]
        if language.startswith("zh"):
            required_audit_sections.append("china_policy")
        elif language.startswith("en"):
            required_audit_sections.append("target_market_policy")
    else:
        # Preserve the original V1/V2 audit contract for historical episodes.
        # New V3 episodes scope policy fields to the actual language/market.
        required_audit_sections = [
            "facts_and_sources", "china_policy", "platform_policy", "editorial_risk",
        ]
        if quality_contract_version >= 2:
            required_audit_sections.append("editorial_quality")
    missing_audit_sections = [name for name in required_audit_sections if not content_audit.get(name)]
    if missing_audit_sections:
        errors.append(f"content audit is incomplete: {missing_audit_sections}")
    checks["content_audit"] = {
        "status": content_audit.get("status"),
        "sections": {name: bool(content_audit.get(name)) for name in required_audit_sections},
    }
    cards = content.get("cards") if isinstance(content, dict) else None
    if not isinstance(cards, list) or not cards:
        errors.append("content_manifest.json must contain non-empty cards")
        cards = []
    elif str(cards[0].get("tts_text") or "").strip():
        errors.append("first card must be silent and reuse the cover")
    if quality_contract_version >= 2 and cards:
        allowed_purposes = {"hook", "fact", "evidence", "mechanism", "boundary", "viewer_impact", "question"}
        for card in cards[1:]:
            purpose = str(card.get("purpose") or "")
            if purpose not in allowed_purposes:
                errors.append(f"spoken card {card.get('id')} needs a valid editorial purpose, got {purpose!r}")

    video_cfg = content.get("video") if isinstance(content, dict) else {}
    tail_config = float(video_cfg.get("tail_hold_seconds", -1))
    if abs(tail_config - EXPECTED_TAIL) > 0.001:
        errors.append(f"tail_hold_seconds must be {EXPECTED_TAIL}, got {tail_config}")
    if abs(float(episode.get("tail_hold_sec", -1)) - EXPECTED_TAIL) > 0.001:
        errors.append("episode_manifest tail_hold_sec must be 0.8")

    for card in cards:
        image = root / str(card.get("image") or "")
        if not image.is_file():
            errors.append(f"missing card image for {card.get('id')}: {image}")
    source_items = sources.get("sources") if isinstance(sources, dict) else None
    if not isinstance(source_items, list) or not source_items:
        errors.append("source_manifest.json must contain at least one source")

    final_dir = root / "final"
    videos = sorted(final_dir.glob("*.mp4")) if final_dir.is_dir() else []
    if len(videos) != 1:
        errors.append(f"final/ must contain exactly one mp4, found {len(videos)}")
        video = videos[0] if videos else None
    else:
        video = videos[0]
    covers = sorted([*final_dir.glob("cover*.png"), *final_dir.glob("cover*.jpg"), *final_dir.glob("cover*.webp")])
    if not covers:
        errors.append("final/ must contain a cover image")
    if any(str((card.get("animation") or {}).get("engine") or "legacy") == "scene_v2" for card in cards):
        motion_report_path = final_dir / "motion_quality_report.json"
        if not motion_report_path.is_file():
            errors.append("scene_v2 release requires final/motion_quality_report.json")
        else:
            motion_report = read_json(motion_report_path)
            if motion_report.get("blocking") or motion_report.get("status") == "FAIL":
                errors.append("motion quality report contains blocking failures")
            checks["motion_quality"] = {
                "status": motion_report.get("status"),
                "warnings": len(motion_report.get("warnings") or []),
            }

    publish_text = required["publish_info"].read_text(encoding="utf-8")
    lower_copy = publish_text.lower()
    leaked = [pattern for pattern in PRIVATE_COPY_PATTERNS if re.search(pattern, lower_copy, flags=re.IGNORECASE)]
    if leaked:
        errors.append(f"publish_info.md exposes production notes: {leaked}")
    if language.startswith("zh"):
        if not re.search(r"^##\s*(?:标题|发布标题)", publish_text, flags=re.MULTILINE):
            errors.append("Chinese publish_info.md needs a ## 标题 section")
        if not re.search(r"^##\s*(?:简介|发布文案)", publish_text, flags=re.MULTILINE):
            errors.append("Chinese publish_info.md needs a ## 简介 section")
    elif language.startswith("en"):
        if not re.search(r"^##\s*Title", publish_text, flags=re.IGNORECASE | re.MULTILINE):
            errors.append("English publish_info.md needs a ## Title section")
        if not re.search(r"^##\s*Description", publish_text, flags=re.IGNORECASE | re.MULTILINE):
            errors.append("English publish_info.md needs a ## Description section")
    else:
        errors.append(f"unsupported or missing language: {language}")

    srt_text = required["subtitles"].read_text(encoding="utf-8")
    cue_count = len(re.findall(r"(?m)^\d+\s*$", srt_text))
    speech_count = sum(1 for card in cards if str(card.get("tts_text") or "").strip())
    expected_cue_count = sum(
        len(card.get("caption_chunks") or [card.get("caption_text")])
        for card in cards if str(card.get("tts_text") or "").strip()
    )
    if cue_count != expected_cue_count:
        errors.append(f"SRT cue count {cue_count} != expected caption page count {expected_cue_count}")
    checks["card_count"] = len(cards)
    checks["speech_card_count"] = speech_count
    checks["expected_caption_page_count"] = expected_cue_count
    checks["srt_cue_count"] = cue_count

    if video:
        media = probe(video)
        streams = media.get("streams") or []
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        duration_sec = float((media.get("format") or {}).get("duration") or 0)
        if not video_stream:
            errors.append("final mp4 has no video stream")
        else:
            if video_stream.get("codec_name") != "h264":
                errors.append(f"video codec must be h264, got {video_stream.get('codec_name')}")
            if int(video_stream.get("width") or 0) != EXPECTED_WIDTH or int(video_stream.get("height") or 0) != EXPECTED_HEIGHT:
                errors.append(f"video canvas must be 1080x1920, got {video_stream.get('width')}x{video_stream.get('height')}")
            measured_fps = fps_value(str(video_stream.get("r_frame_rate") or "0/1"))
            if abs(measured_fps - EXPECTED_FPS) > 0.01:
                errors.append(f"video fps must be 30, got {measured_fps}")
        if not audio_stream:
            errors.append("final mp4 has no audio stream")
        elif audio_stream.get("codec_name") != "aac":
            errors.append(f"audio codec must be aac, got {audio_stream.get('codec_name')}")
        tail_sec = trailing_silence(video, duration_sec)
        if tail_sec is None:
            errors.append("could not measure final trailing silence")
        elif not TAIL_MIN <= round(tail_sec, 3) <= TAIL_MAX:
            errors.append(f"encoded trailing silence must be {TAIL_MIN:.2f}-{TAIL_MAX:.2f}s, got {tail_sec:.3f}s")
        checks.update({
            "video": str(video), "video_duration_sec": round(duration_sec, 3),
            "encoded_trailing_silence_sec": None if tail_sec is None else round(tail_sec, 3),
            "video_codec": None if not video_stream else video_stream.get("codec_name"),
            "audio_codec": None if not audio_stream else audio_stream.get("codec_name"),
        })

    if "/creative_work/videos/" not in str(root):
        warnings.append("episode is outside the default creative_work/videos catalogue")
    prompt_records = list((root / "prompts").glob("*.md")) if (root / "prompts").is_dir() else []
    if not prompt_records:
        warnings.append("no Imagen prompt record found under prompts/")

    try:
        visual_report = scan_episode(root)
        visual_path = root / "final" / "visual_risk_report.json"
        visual_path.write_text(json.dumps(visual_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        checks["visual_risk_scan"] = {
            "status": visual_report["status"],
            "advisory_only": True,
            "blocking": False,
            "report": str(visual_path),
            "images_and_frames_scanned": visual_report["images_and_frames_scanned"],
            "warning_count": len(visual_report["warnings"]),
        }
        for finding in visual_report["warnings"]:
            location = finding["asset"]
            if finding.get("timestamp_sec") is not None:
                location += f" @ {finding['timestamp_sec']:.3f}s"
            warnings.append(
                f"visual advisory only: {finding['type']} ({finding['confidence']}) at {location}"
            )
    # The advisory scanner must never turn an otherwise valid release into a hard failure.
    except Exception as exc:  # noqa: BLE001
        checks["visual_risk_scan"] = {"status": "UNAVAILABLE", "advisory_only": True, "blocking": False}
        warnings.append(f"visual advisory scan unavailable: {type(exc).__name__}: {exc}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "root": str(root), "errors": errors, "warnings": warnings, "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Global Hotspot Radar episode directory")
    parser.add_argument("episode_root", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    report = validate(args.episode_root.resolve())
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.json_output:
        args.json_output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
