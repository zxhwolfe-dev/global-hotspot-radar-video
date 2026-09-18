"""Real FFmpeg roundtrips; synthetic colored frames/tones, NOT news or TTS quality."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import wave

import numpy as np
from PIL import Image
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import render_episode as renderer
from ghr_renderer.audio_artifacts import BUNDLE_FILES, INCOMPLETE, audio_write, select_bundle, stage_bundle
from ghr_renderer.episode_lock import episode_lock

MEDIA = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def hashes(directory: Path) -> dict[str, str]:
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def seed(root: Path, *, folder: str = "final") -> tuple[dict, list]:
    (root / "generated").mkdir(parents=True, exist_ok=True)
    for name, color in (("cover", (40, 80, 150)), ("story", (150, 60, 40))):
        Image.new("RGB", (180, 320), color).save(root / "generated" / f"{name}.png")
    data = {
        "project": "synthetic regression only", "language": "en", "quality_contract_version": 1,
        "video": {"width": 180, "height": 320, "fps": 12,
                  "preview_width": 180, "preview_fps": 12, "cover_width": 180, "cover_height": 240,
                  "pre_roll_seconds": 0.35, "tail_hold_seconds": 0.8, "burn_subtitles": True,
                  "subtitle_font": "DejaVu Sans", "subtitle_font_size": 28,
                  "subtitle_margin_v": 40, "subtitle_margin_h": 10},
        "cards": [
            {"id": "cover", "story_id": "intro", "image": "generated/cover.png", "tts_text": "", "caption_text": ""},
            {"id": "story", "story_id": "one", "image": "generated/story.png", "tts_text": "Test only.", "caption_text": "Test only."},
        ],
    }
    timeline = [
        {"id": "cover", "story_id": "intro", "start_sec": 0.0, "end_sec": 0.35, "duration_sec": 0.35},
        {"id": "story", "story_id": "one", "start_sec": 0.35, "spoken_end_sec": 1.35, "end_sec": 2.15, "duration_sec": 1.8},
    ]
    target = root / folder
    target.mkdir(parents=True, exist_ok=True)
    write_tone(target / "master_narration_with_tail.wav")
    (target / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    (target / "master_sync_report.json").write_text(json.dumps({
        "audio_fingerprint": renderer.audio_fingerprint(root, data),
        "narration_and_original_audio_duration_sec": 1.0,
    }), encoding="utf-8")
    renderer.write_subtitle_assets(target, data, timeline)
    (root / "content_manifest.json").write_text(json.dumps(data), encoding="utf-8")
    return data, timeline


def write_tone(path: Path, frequency: int = 440) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        audio.writeframes(b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * frequency * i / 48000))
                                            if 0.35 <= i / 48000 < 1.35 else 0)
                                  for i in range(103200)))


def forbid_provider(*args, **kwargs):
    raise AssertionError("offline operation must not import/configure/invoke a provider")


def assert_media(video: Path) -> None:
    media = renderer.probe(video)
    assert {s["codec_type"]: s["codec_name"] for s in media["streams"]} == {"video": "h264", "audio": "aac"}
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(video), "-t", "0.5",
                              "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"],
                             check=True, capture_output=True).stdout
    samples = np.frombuffer(decoded, dtype="<i2").astype(float)
    spectrum = np.abs(np.fft.rfft(samples))
    peak_hz = int(np.argmax(spectrum)) * 48000 / len(samples)
    assert abs(peak_hz - 440) < 5, f"mux used the wrong cached audio: {peak_hz}Hz"


@pytest.mark.skipif(not MEDIA, reason="ffmpeg and ffprobe required")
@pytest.mark.parametrize("mode", ["preview", "candidate"])
def test_real_final_to_lower_tier_roundtrip(tmp_path, monkeypatch, mode):
    data, _ = seed(tmp_path)
    before = hashes(tmp_path / "final")
    monkeypatch.setattr(renderer, "_load_tts_provider", forbid_provider)
    # A stray preview WAV must never be combined with the final report/timeline.
    (tmp_path / "preview").mkdir()
    write_tone(tmp_path / "preview" / "master_narration_with_tail.wav", frequency=880)
    timeline, _, seconds, out = renderer.reuse_audio(tmp_path, data, mode=mode)
    video, _, _ = renderer.render_video(tmp_path, data, timeline, seconds, mode=mode, artifacts_dir=out)
    assert_media(video)
    assert hashes(tmp_path / "final") == before
    assert out == tmp_path / "preview"
    report = json.loads((out / f"technical_validation_{mode}.json").read_text())
    assert Path(report["subtitle_sidecar"]) == out / "subtitles.srt"
    assert Path(report["master_audio"]) == out / "master_narration_with_tail.wav"
    assert all((out / name).is_file() for name in BUNDLE_FILES)


@pytest.mark.skipif(not MEDIA, reason="ffmpeg and ffprobe required")
def test_real_preview_to_release_without_an_existing_release(tmp_path, monkeypatch):
    data, _ = seed(tmp_path, folder="preview")
    before = hashes(tmp_path / "preview")
    monkeypatch.setattr(renderer, "_load_tts_provider", forbid_provider)
    timeline, _, seconds, out = renderer.reuse_audio(tmp_path, data, mode="release")
    video, _, _ = renderer.render_video(tmp_path, data, timeline, seconds, mode="release", artifacts_dir=out)
    renderer.update_episode_manifest(tmp_path, data, video, seconds)
    assert_media(video)
    assert out == tmp_path / "final"
    assert all((out / name).is_file() for name in BUNDLE_FILES)
    after = hashes(tmp_path / "preview")
    assert all(after[name] == digest for name, digest in before.items())


@pytest.mark.skipif(not MEDIA, reason="ffmpeg and ffprobe required")
def test_stale_complete_preview_does_not_shadow_matching_final(tmp_path):
    data, _ = seed(tmp_path)
    stage_bundle(tmp_path / "final", tmp_path / "preview")
    (tmp_path / "preview" / "master_sync_report.json").write_text('{"audio_fingerprint":"stale"}')
    _, _, _, out = renderer.reuse_audio(tmp_path, data, mode="candidate")
    assert json.loads((out / "master_sync_report.json").read_text())["audio_fingerprint"] == renderer.audio_fingerprint(tmp_path, data)


@pytest.mark.skipif(not MEDIA, reason="ffmpeg and ffprobe required")
def test_missing_derived_subtitles_are_rebuilt_not_a_cache_miss(tmp_path):
    data, _ = seed(tmp_path)
    (tmp_path / "final" / "subtitles.srt").unlink()
    (tmp_path / "final" / "subtitles_burn.ass").unlink()
    renderer.reuse_audio(tmp_path, data, mode="preview")
    assert "Test only." in (tmp_path / "preview" / "subtitles.srt").read_text()


@pytest.mark.parametrize("field,value", [("duration_sec", float("nan")), ("start_sec", -1),
                                          ("spoken_end_sec", 9), ("end_sec", 0.1), ("id", "wrong")])
def test_corrupt_timeline_is_not_reused_or_repaired_silently(tmp_path, field, value):
    data, timeline = seed(tmp_path)
    timeline[1][field] = value
    (tmp_path / "final" / "timeline.json").write_text(json.dumps(timeline))
    before = hashes(tmp_path / "final")
    with pytest.raises(ValueError, match="no matching complete bundle"):
        select_bundle(tmp_path, data, renderer.audio_fingerprint(tmp_path, data), "preview", lambda _: 2.15)
    assert not (tmp_path / "preview").exists()
    assert hashes(tmp_path / "final") == before


def test_interrupted_stage_is_explicit_and_can_fall_back_to_good_source(tmp_path):
    data, _ = seed(tmp_path)
    stage_bundle(tmp_path / "final", tmp_path / "preview")
    with pytest.raises(RuntimeError):
        with audio_write(tmp_path / "preview"):
            raise RuntimeError("simulated interruption")
    selected, _, _, _ = select_bundle(tmp_path, data, renderer.audio_fingerprint(tmp_path, data), "preview", lambda _: 2.15)
    assert selected == tmp_path / "final"
    stage_bundle(selected, tmp_path / "preview")
    assert not (tmp_path / "preview" / INCOMPLETE).exists()


def test_failed_copy_does_not_touch_existing_target(tmp_path, monkeypatch):
    seed(tmp_path)
    target = tmp_path / "preview"
    target.mkdir()
    for name in BUNDLE_FILES:
        (target / name).write_bytes(b"untouched")
    before = hashes(target)
    original = shutil.copyfile
    def fail_report(src, dst, **kwargs):
        if Path(src).name == "master_sync_report.json":
            raise OSError("synthetic disk error")
        return original(src, dst, **kwargs)
    monkeypatch.setattr(shutil, "copyfile", fail_report)
    with pytest.raises(OSError):
        stage_bundle(tmp_path / "final", target)
    assert hashes(target) == before


@pytest.mark.skipif(not MEDIA, reason="ffmpeg and ffprobe required")
def test_real_retained_tts_rebuild_never_loads_provider_or_env(tmp_path, monkeypatch):
    data, _ = seed(tmp_path)
    before = hashes(tmp_path / "final")
    audio = tmp_path / "audio"
    audio.mkdir()
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp_path / "final" / "master_narration_with_tail.wav"),
                    "-c:a", "libmp3lame", str(audio / "narration_combined.mp3")], check=True)
    (audio / "tts_cache_meta.json").write_text(json.dumps({"tts_fingerprint": renderer.tts_fingerprint(data)}))
    (audio / "line_timings.json").write_text(json.dumps([{"card_id": "story", "provider_offset_sec": 0.35, "provider_end_sec": 1.35}]))
    monkeypatch.setattr(renderer, "_load_tts_provider", forbid_provider)
    monkeypatch.setattr(renderer, "load_dotenv", forbid_provider)
    writer_calls = []
    writer = renderer.write_subtitle_assets
    def counted(*args, **kwargs):
        writer_calls.append(args[0])
        return writer(*args, **kwargs)
    monkeypatch.setattr(renderer, "write_subtitle_assets", counted)
    timeline, _, seconds, out = renderer.build_audio(tmp_path, data, reuse_tts=True, mode="preview")
    assert out == tmp_path / "preview"
    video, _, _ = renderer.render_video(tmp_path, data, timeline, seconds, mode="preview", artifacts_dir=out)
    assert_media(video)
    timeline, _, seconds, out = renderer.reuse_audio(tmp_path, data, mode="candidate")
    video, _, _ = renderer.render_video(tmp_path, data, timeline, seconds, mode="candidate", artifacts_dir=out)
    assert_media(video)
    assert writer_calls == [tmp_path / "preview", tmp_path / "preview"]
    assert hashes(tmp_path / "final") == before
    assert not (out / INCOMPLETE).exists()


def test_renderer_import_and_help_without_host_provider(tmp_path):
    code = f'''import sys, importlib.abc
class BlockProvider(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith("ai_douyin_video_pipeline"):
   raise AssertionError("provider import during offline entry")
sys.meta_path.insert(0, BlockProvider())
sys.path.insert(0, {str(SCRIPTS)!r})
import render_episode
sys.argv = ["render_episode.py", "--help"]
render_episode.main()
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--reuse-tts" in result.stdout


@pytest.mark.parametrize("mode", ["preview", "candidate", "release"])
def test_same_project_lock_blocks_cli_before_it_changes_artifacts(tmp_path, mode):
    seed(tmp_path)
    before = hashes(tmp_path)
    with episode_lock(tmp_path):
        result = subprocess.run([sys.executable, str(SCRIPTS / "render_episode.py"), "--manifest",
                                 str(tmp_path / "content_manifest.json"), "--reuse-audio", "--mode", mode],
                                capture_output=True, text=True)
        assert result.returncode != 0
        assert "already being rendered" in result.stderr
    after = hashes(tmp_path)
    after.pop(".ghr-render.lock")
    assert after == before
    with episode_lock(tmp_path):
        pass  # Same lock can be acquired once the owner has left.


def test_lock_releases_after_exception_and_allows_other_projects(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    with pytest.raises(ValueError):
        with episode_lock(a), episode_lock(b):
            raise ValueError("synthetic failure")
    with episode_lock(a):
        pass


@pytest.mark.parametrize("mode", ["typo", "", None])
def test_unknown_mode_never_silently_selects_release(tmp_path, mode):
    data, _ = seed(tmp_path)
    with pytest.raises(ValueError, match="unsupported render mode"):
        select_bundle(tmp_path, data, "none", mode, lambda _: 2.15)
