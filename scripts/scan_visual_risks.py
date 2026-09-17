#!/usr/bin/env python3
"""Warn about platform UI, account identifiers, QR codes, and watermarks.

This scanner is deliberately advisory: findings never change the episode validator's
PASS/FAIL result and never remove or rewrite an asset.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import cv2
from rapidocr_onnxruntime import RapidOCR

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PLATFORM_TERMS = {
    "bilibili": ("bilibili", "哔哩哔哩", "b站"),
    "douyin": ("抖音", "douyin"),
    "tiktok": ("tiktok",),
    "kuaishou": ("快手", "kuaishou"),
    "xiaohongshu": ("小红书", "rednote"),
    "youtube": ("youtube",),
    "instagram": ("instagram",),
    "facebook": ("facebook",),
    "weibo": ("微博", "weibo"),
    "wechat_channels": ("视频号", "微信视频号"),
    "reddit": ("reddit",),
}
UI_TERMS = (
    "关注", "粉丝", "发消息", "下载客户端", "排行榜", "播放量", "点赞", "评论", "分享",
    "follow", "followers", "following", "subscribe", "subscribers", "views", "likes", "comments", "share",
)
ACCOUNT_TERMS = ("官方账号", "官方帐号", "账号", "帐号", "认证", "verified", "creator", "channel")
SOURCE_DOMAINS = {
    "bilibili.com": "bilibili",
    "douyin.com": "douyin",
    "tiktok.com": "tiktok",
    "kuaishou.com": "kuaishou",
    "xiaohongshu.com": "xiaohongshu",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "weibo.com": "weibo",
}


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=True, text=True)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def probe_duration(video: Path) -> float:
    result = run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    )
    return float(result.stdout.strip())


def collect_images(root: Path) -> list[Path]:
    paths: list[Path] = []
    content = read_json(root / "content_manifest.json")
    for card in content.get("cards", []):
        if isinstance(card, dict) and card.get("image"):
            paths.append(root / str(card["image"]))
        animation = card.get("animation") if isinstance(card, dict) else None
        if isinstance(animation, dict):
            for layer in animation.get("layers") or []:
                if isinstance(layer, dict) and layer.get("asset"):
                    paths.append(root / str(layer["asset"]))
    final_dir = root / "final"
    if final_dir.is_dir():
        paths.extend(path for path in final_dir.glob("cover*") if path.suffix.casefold() in IMAGE_SUFFIXES)
    return list(dict.fromkeys(path.resolve() for path in paths if path.is_file()))


def sample_video(video: Path, count: int, directory: Path) -> list[tuple[Path, float]]:
    duration = probe_duration(video)
    timestamps = [(index + 0.5) * duration / count for index in range(count)] if count > 0 else []
    sync = read_json(video.parent / "master_sync_report.json")
    for window in sync.get("original_sound_windows", []):
        try:
            start, end = float(window["start_sec"]), float(window["end_sec"])
            timestamps.extend((start + 0.05, (start + end) / 2, max(start, end - 0.05)))
        except (KeyError, TypeError, ValueError):
            continue
    technical = read_json(video.parent / "technical_validation.json")
    for window in technical.get("animation_windows", []):
        try:
            start, end = float(window["start_sec"]), float(window["end_sec"])
            timestamps.extend((start + min(0.25, (end - start) / 4), (start + end) / 2, max(start, end - 0.15)))
        except (KeyError, TypeError, ValueError):
            continue
    output: list[tuple[Path, float]] = []
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video for visual scan: {video}")
    try:
        for index, timestamp in enumerate(sorted({round(max(0.0, min(duration - 0.001, value)), 3) for value in timestamps})):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, image = capture.read()
            if not ok or image is None:
                continue
            frame = directory / f"frame_{index + 1:03d}_{timestamp:.3f}.jpg"
            cv2.imwrite(str(frame), image, [int(cv2.IMWRITE_JPEG_QUALITY), 93])
            output.append((frame, timestamp))
    finally:
        capture.release()
    return output


def qr_values(image: Any) -> list[str]:
    detector = cv2.QRCodeDetector()
    values: list[str] = []
    try:
        detected, decoded, _, _ = detector.detectAndDecodeMulti(image)
        if detected:
            values.extend(value or "undecoded_qr" for value in decoded)
    except cv2.error:
        value, points, _ = detector.detectAndDecode(image)
        if points is not None:
            values.append(value or "undecoded_qr")
    return values


def platform_hits(text: str) -> list[str]:
    lowered = text.casefold()
    return [name for name, aliases in PLATFORM_TERMS.items() if any(alias.casefold() in lowered for alias in aliases)]


def scan_image(path: Path, engine: RapidOCR, *, asset: str, timestamp: float | None = None) -> dict[str, Any]:
    image = cv2.imread(str(path))
    if image is None:
        return {"asset": asset, "timestamp_sec": timestamp, "error": "image_decode_failed", "warnings": []}
    result, _ = engine(image)
    words = []
    for row in result or []:
        if len(row) < 3:
            continue
        text, confidence = str(row[1]).strip(), float(row[2])
        if text and confidence >= 0.55:
            words.append({"text": text, "confidence": round(confidence, 3), "box": row[0]})
    joined = " ".join(item["text"] for item in words)
    lowered = joined.casefold()
    platforms = platform_hits(joined)
    ui_hits = sorted({term for term in UI_TERMS if term.casefold() in lowered})
    account_hits = sorted({term for term in ACCOUNT_TERMS if term.casefold() in lowered})
    handles = sorted(set(re.findall(r"(?<!\w)@[A-Za-z0-9_.-]{3,}", joined)))
    qrs = qr_values(image)
    warnings: list[dict[str, Any]] = []
    if qrs:
        warnings.append({"type": "qr_code", "confidence": "high", "evidence": qrs})
    if platforms:
        warnings.append({"type": "platform_brand_or_watermark", "confidence": "high", "evidence": platforms})
    if handles:
        warnings.append({"type": "account_identifier", "confidence": "high", "evidence": handles})
    if account_hits and ui_hits:
        warnings.append({
            "type": "account_profile_ui", "confidence": "high",
            "evidence": {"account_terms": account_hits, "ui_terms": ui_hits},
        })
    elif len(ui_hits) >= 2:
        warnings.append({"type": "platform_interface", "confidence": "medium", "evidence": ui_hits})
    return {
        "asset": asset,
        "timestamp_sec": timestamp,
        "ocr_text": [item["text"] for item in words],
        "warnings": warnings,
    }


def manifest_platform_sources(root: Path) -> list[dict[str, str]]:
    data = read_json(root / "source_manifest.json")
    matches: list[dict[str, str]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str) and value.startswith(("http://", "https://")):
            host = urlsplit(value).netloc.casefold().removeprefix("www.")
            for domain, platform in SOURCE_DOMAINS.items():
                if host == domain or host.endswith("." + domain):
                    matches.append({"platform": platform, "url": value})
                    break

    visit(data)
    return [dict(item) for item in {tuple(sorted(match.items())) for match in matches}]


def scan_episode(root: Path, video_samples: int | None = None) -> dict[str, Any]:
    root = root.resolve()
    engine = RapidOCR()
    findings: list[dict[str, Any]] = []
    for image in collect_images(root):
        findings.append(scan_image(image, engine, asset=str(image.relative_to(root))))
    final_videos = sorted((root / "final").glob("*.mp4")) if (root / "final").is_dir() else []
    resolved_video_samples = 0 if video_samples is None else video_samples
    dynamic_video_samples = 0
    if len(final_videos) == 1:
        with tempfile.TemporaryDirectory(prefix="ghr_visual_scan_") as temp:
            for frame, timestamp in sample_video(final_videos[0], resolved_video_samples, Path(temp)):
                dynamic_video_samples += 1
                findings.append(
                    scan_image(frame, engine, asset=f"{final_videos[0].relative_to(root)}#frame", timestamp=timestamp)
                )
    warnings = [
        {"asset": item["asset"], "timestamp_sec": item.get("timestamp_sec"), **warning}
        for item in findings for warning in item.get("warnings", [])
    ]
    report = {
        "status": "WARN" if warnings else "CLEAR",
        "advisory_only": True,
        "blocking": False,
        "root": str(root),
        "images_and_frames_scanned": len(findings),
        "uniform_video_samples": resolved_video_samples or 0,
        "dynamic_window_video_samples": dynamic_video_samples,
        "warnings": warnings,
        "platform_source_context": manifest_platform_sources(root),
        "review_note": (
            "自动结果只用于发布前人工复核。平台标志、账号、二维码或界面可能增加审核/引流风险，"
            "但不自动等同于侵权，也不会阻止发布、删除素材或改动成片。"
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episode_root", type=Path)
    parser.add_argument("--video-samples", type=int, help="Override automatic duration-based frame sampling")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    report = scan_episode(
        args.episode_root,
        None if args.video_samples is None else max(0, args.video_samples),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    output = args.json_output or args.episode_root.resolve() / "final" / "visual_risk_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
