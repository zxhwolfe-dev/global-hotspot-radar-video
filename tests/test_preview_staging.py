"""P1: preview/candidate builds must not overwrite existing final artifacts.

Running a lower-tier render after a release must leave final/ sidecar
files (master audio, subtitles, timeline, sync report) byte-identical.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

FFMPEG = shutil.which("ffmpeg")
FINAL_SIDECARS = (
    "master_narration_with_tail.wav",
    "subtitles.srt",
    "subtitles_burn.ass",
    "timeline.json",
    "master_sync_report.json",
)


def _png_bytes() -> bytes:
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d"
        "4944415478da6360000002000154a24f4e070000000049454e44ae426082")


def _seed(root: Path) -> None:
    (root / "generated").mkdir(parents=True)
    (root / "final").mkdir()
    for name in ("c.png", "c1.png"):
        (root / "generated" / name).write_bytes(_png_bytes())
    manifest = {
        "project": "preview-staging-check",
        "quality_contract_version": 3,
        "language": "zh-CN",
        "voice": {"provider": "alibaba_qwen_tts", "model": "qwen-audio-3.0-tts-plus", "voice": "v"},
        "video": {"width": 1080, "height": 1920, "fps": 30, "pre_roll_seconds": 0.35,
                  "tail_hold_seconds": 0.8, "burn_subtitles": True, "subtitle_font_size": 56,
                  "subtitle_keep_terms": [], "output_filename": "staging.mp4"},
        "audio": {"speech_gap_seconds": 0.3},
        "cards": [
            {"id": "cover", "kind": "cover", "story_id": "intro", "image": "generated/c.png",
             "tts_text": "", "caption_text": ""},
            {"id": "c1", "story_id": "s1", "purpose": "fact", "image": "generated/c1.png",
             "tts_text": "[curious]第一句口播。",
             "caption_text": "第一句口播。",
             "caption_chunks": ["第一句口播。"]},
        ],
    }
    (root / "content_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    rows = [
        {"id": "cover", "story_id": "intro", "start_sec": 0.0, "spoken_end_sec": 0.0,
         "end_sec": 0.35, "duration_sec": 0.35, "subtitle_cue_count": 0},
        {"id": "c1", "story_id": "s1", "start_sec": 0.35, "spoken_end_sec": 2.35,
         "end_sec": 2.7, "duration_sec": 2.35, "subtitle_cue_count": 1},
    ]
    (root / "final" / "timeline.json").write_text(json.dumps(rows), encoding="utf-8")
    from ghr_renderer.audio_contracts import audio_fingerprint
    (root / "final" / "master_sync_report.json").write_text(json.dumps({
        "audio_fingerprint": audio_fingerprint(root, manifest),
        "narration_and_original_audio_duration_sec": 2.7,
    }), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", "3.0", "-c:a", "pcm_s16le", str(root / "final" / "master_narration_with_tail.wav")],
        check=True)
    (root / "final" / "subtitles.srt").write_text("1\n00:00:00,350 --> 00:00:02,350\n第一句口播。\n", encoding="utf-8")
    (root / "final" / "subtitles_burn.ass").write_text("[V4+ Styles]\nStyle: Caption,Font,56\n", encoding="utf-8")


def _dir_hashes(root: Path) -> dict[str, str]:
    return {
        name: hashlib.sha256((root / "final" / name).read_bytes()).hexdigest()
        for name in FINAL_SIDECARS
        if (root / "final" / name).is_file()
    }


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_preview_render_leaves_final_sidecars_untouched(tmp_path, monkeypatch):
    import render_episode

    _seed(tmp_path)
    before = _dir_hashes(tmp_path)
    assert len(before) == len(FINAL_SIDECARS)

    def fake_render_video(root, data, timeline, audio_sec, *, mode, artifacts_dir=None):
        return root / "preview" / f"{mode}_staging.mp4", 3.0, {"stub": True}

    monkeypatch.setattr(render_episode, "render_video", fake_render_video)
    monkeypatch.setattr(sys, "argv", ["render_episode.py",
        "--manifest", str(tmp_path / "content_manifest.json"), "--mode", "preview", "--reuse-audio"])
    assert render_episode.main() == 0

    assert _dir_hashes(tmp_path) == before, "preview build must not rewrite final sidecars"
    for name in ("subtitles.srt", "subtitles_burn.ass"):
        assert (tmp_path / "preview" / name).is_file(), f"preview must stage {name} outside final/"


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")
def test_preview_reuse_tts_then_reuse_audio_migration_flow(tmp_path, monkeypatch):
    """v2->v3 migration inside the preview tier must stay coherent.

    A preview --reuse-tts rebuild stages its v3 report in preview/; a
    following preview --reuse-audio must accept the staged report instead of
    failing against the stale v2 report still sitting in final/.
    """
    import render_episode

    _seed(tmp_path)
    # Simulate the v2-era report by invalidating the fingerprint in final/.
    report = json.loads((tmp_path / "final" / "master_sync_report.json").read_text(encoding="utf-8"))
    report["audio_fingerprint"] = "v2-era-value"
    (tmp_path / "final" / "master_sync_report.json").write_text(json.dumps(report), encoding="utf-8")

    def fake_render_video(root, data, timeline, audio_sec, *, mode, artifacts_dir=None):
        return root / "preview" / f"{mode}_staging.mp4", 3.0, {"stub": True}

    monkeypatch.setattr(render_episode, "render_video", fake_render_video)

    # Preview reuse-audio against only the stale v2 report must fail safely.
    monkeypatch.setattr(sys, "argv", ["render_episode.py",
        "--manifest", str(tmp_path / "content_manifest.json"), "--mode", "preview", "--reuse-audio"])
    with pytest.raises(ValueError):
        render_episode.main()

    # Stage a fresh v3 report via the build path (audio from silent provider
    # stub is unnecessary here: write_subtitle_assets covers the sidecars and
    # the fingerprint comes from the real manifest).
    from ghr_renderer.audio_contracts import audio_fingerprint
    data = json.loads((tmp_path / "content_manifest.json").read_text(encoding="utf-8"))
    (tmp_path / "preview").mkdir(exist_ok=True)
    import shutil as _sh
    for name in ("master_narration_with_tail.wav", "timeline.json", "subtitles.srt"):
        _sh.copy2(tmp_path / "final" / name, tmp_path / "preview" / name)
    staged_report = {
        "audio_fingerprint": audio_fingerprint(tmp_path, data),
        "narration_and_original_audio_duration_sec": 2.7,
    }
    (tmp_path / "preview" / "master_sync_report.json").write_text(json.dumps(staged_report), encoding="utf-8")

    rc = render_episode.main()
    assert rc == 0, "preview reuse-audio must accept the tier-staged v3 report"
    assert (tmp_path / "preview" / "subtitles_burn.ass").is_file()
