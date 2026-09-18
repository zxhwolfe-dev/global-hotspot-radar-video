"""P1: --reuse-audio must rebuild subtitle assets from current settings.

Regression contract: reusing audio must not silently keep stale subtitle
layout (font size, margins, protected terms) from a previous build.
Cues are rebuilt from the saved timeline's spoken boundaries plus the
current manifest captions; the audio fingerprint itself must not change.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

FFMPEG = shutil.which("ffmpeg")


def _manifest(font_size: int, keep_terms: list[str]) -> dict:
    return {
        "project": "subtitle-reuse-check",
        "quality_contract_version": 3,
        "language": "zh-CN",
        "voice": {"provider": "alibaba_qwen_tts", "model": "qwen-audio-3.0-tts-plus", "voice": "v"},
        "video": {
            "width": 1080, "height": 1920, "fps": 30,
            "pre_roll_seconds": 0.35, "tail_hold_seconds": 0.8,
            "burn_subtitles": True,
            "subtitle_font_size": font_size,
            "subtitle_margin_h": 70, "subtitle_margin_v": 210,
            "subtitle_keep_terms": keep_terms,
            "output_filename": "reuse_subtitles.mp4",
        },
        "audio": {"speech_gap_seconds": 0.3},
        "cards": [
            {"id": "cover", "kind": "cover", "story_id": "intro", "image": "generated/c.png",
             "tts_text": "", "caption_text": ""},
            {"id": "c1", "story_id": "s1", "purpose": "fact", "image": "generated/c1.png",
             "tts_text": "[curious]OpenAI 发布了新模型。",
             "caption_text": "OpenAI 发布了新模型。",
             "caption_chunks": ["OpenAI 发布了", "新模型。"]},
        ],
    }


def _png_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d"
        "4944415478da6360000002000154a24f4e070000000049454e44ae426082")


def _seed_project(root: Path, font_size: int) -> None:
    import render_episode
    from ghr_renderer.audio_contracts import audio_fingerprint

    (root / "generated").mkdir(parents=True)
    (root / "final").mkdir()
    for name in ("c.png", "c1.png"):
        (root / "generated" / name).write_bytes(_png_bytes())
    data = _manifest(font_size, ["OpenAI"])
    (root / "content_manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rows = [
        {"id": "cover", "story_id": "intro", "start_sec": 0.0, "spoken_end_sec": 0.0,
         "end_sec": 0.35, "duration_sec": 0.35, "subtitle_cue_count": 0},
        {"id": "c1", "story_id": "s1", "start_sec": 0.35, "spoken_end_sec": 3.35,
         "end_sec": 3.7, "duration_sec": 3.35, "subtitle_cue_count": 2},
    ]
    (root / "final" / "timeline.json").write_text(json.dumps(rows), encoding="utf-8")
    fingerprint = audio_fingerprint(root, data)
    (root / "final" / "master_sync_report.json").write_text(json.dumps({
        "audio_fingerprint": fingerprint,
        "narration_and_original_audio_duration_sec": 3.7,
    }), encoding="utf-8")
    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", "4.0", "-c:a", "pcm_s16le", str(root / "final" / "master_narration_with_tail.wav")],
        check=True)
    (root / "final" / "subtitles.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\n旧字幕\n", encoding="utf-8")
    (root / "final" / "subtitles_burn.ass").write_text("Style: Caption,Font,999\n", encoding="utf-8")
    return fingerprint


def _font_size_in_ass(path: Path) -> float:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^Style: Caption,[^,]+,([0-9.]+),", text, re.M)
    assert match, f"no Caption style in ASS:\n{text[:400]}"
    return float(match.group(1))


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_reuse_audio_rebuilds_ass_and_srt_from_current_settings(tmp_path, monkeypatch):
    import render_episode

    fingerprint_before = _seed_project(tmp_path, font_size=56)

    def fake_render_video(root, data, timeline, audio_sec, *, mode):
        return root / "final" / "reuse_subtitles.mp4", 4.0, {"stub": True}

    monkeypatch.setattr(render_episode, "render_video", fake_render_video)
    monkeypatch.setattr(sys, "argv", ["render_episode.py",
        "--manifest", str(tmp_path / "content_manifest.json"), "--mode", "preview", "--reuse-audio"])
    rc = render_episode.main()
    assert rc == 0
    assert _font_size_in_ass(tmp_path / "final" / "subtitles_burn.ass") == 56
    assert "旧字幕" not in (tmp_path / "final" / "subtitles.srt").read_text(encoding="utf-8")
    assert "OpenAI" in (tmp_path / "final" / "subtitles.srt").read_text(encoding="utf-8")

    # Layout-only change: reuse must still succeed (audio fingerprint unchanged)
    # and rebuild assets with the new settings.
    data = json.loads((tmp_path / "content_manifest.json").read_text(encoding="utf-8"))
    data["video"]["subtitle_font_size"] = 72
    data["video"]["subtitle_keep_terms"] = ["OpenAI", "新模型"]
    (tmp_path / "content_manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["render_episode.py",
        "--manifest", str(tmp_path / "content_manifest.json"), "--mode", "preview", "--reuse-audio"])
    rc = render_episode.main()
    assert rc == 0, "layout-only change must not invalidate the audio fingerprint"
    assert _font_size_in_ass(tmp_path / "final" / "subtitles_burn.ass") == 72
    srt_text = (tmp_path / "final" / "subtitles.srt").read_text(encoding="utf-8")
    assert "旧字幕" not in srt_text
    # Pages stay per caption_chunks; timestamps separate them.
    assert "OpenAI 发布了" in srt_text and "新模型。" in srt_text
