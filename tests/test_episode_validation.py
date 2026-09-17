"""Validator integration tests; generated media are synthetic, never news/TTS."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import validate_episode as validator
from test_episode_preflight import manifest, project, put, research, srt


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe required for synthetic media integration")
    path = tmp_path_factory.mktemp("synthetic_media") / "synthetic.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=gray:s=1080x1920:r=30:d=2",
        "-f", "lavfi", "-i", r"aevalsrc=0.1*sin(2*PI*440*t)*lt(t\,1.2):s=48000:d=2",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True, capture_output=True)
    return path


def release(tmp_path, synthetic_video, *, version=1, language="zh-CN"):
    root = project(tmp_path, version=version)
    data = manifest(version=version)
    data["language"] = language
    put(root, "content_manifest.json", data)
    put(root, "episode_manifest.json", {"tail_hold_sec": 0.8})
    audit = {key: "Synthetic test; not a real editorial review" for key in (
        "facts_and_sources", "safety_rights_privacy", "platform_policy", "editorial_risk",
        "editorial_quality", "china_policy", "target_market_policy")}
    audit["status"] = "PASS"
    put(root, "final/content_audit.json", audit)
    put(root, "source_manifest.json", {"sources": [{"title": "Synthetic"}]})
    if version >= 2:
        research(root)
    (root / "final/subtitles.srt").write_text(srt())
    (root / "publish_info.md").write_text("## 标题\n合成测试\n## 简介\n不是新闻\n" if language.startswith("zh")
        else "## Title\nSynthetic\n## Description\nNot news\n")
    # The validator only checks cover presence; visual decoding is not under test.
    (root / "final/cover.png").write_bytes(b"synthetic cover placeholder")
    shutil.copyfile(synthetic_video, root / "final/test.mp4")
    return root


@pytest.mark.parametrize("version,language", [(1,"zh-CN"), (2,"zh-CN"), (3,"zh-CN"), (3,"en")])
def test_real_synthetic_media_and_legacy_research_compatibility(tmp_path, synthetic_video, version, language, monkeypatch):
    root = release(tmp_path, synthetic_video, version=version, language=language)
    def unavailable(_):
        raise ImportError("synthetic optional scanner unavailable")
    monkeypatch.setattr(validator, "scan_episode", unavailable)
    result = validator.validate(root)
    assert result["status"] == "PASS", result
    assert result["checks"]["srt_cue_count"] == 1
    assert 0.72 <= result["checks"]["encoded_trailing_silence_sec"] <= 0.90
    assert result["checks"]["visual_risk_scan"]["status"] == "UNAVAILABLE"
    assert result["checks"]["preflight"]["checks"]["research"]["status"] == ("NOT_CONFIGURED" if version == 1 else "STRUCTURE_OK")


def test_changed_srt_same_cue_count_fails_actual_validator(tmp_path, synthetic_video):
    root = release(tmp_path, synthetic_video)
    (root / "final/subtitles.srt").write_text(srt("2027"))
    result = validator.validate(root)
    assert result["status"] == "FAIL"
    assert "text differs" in str(result["errors"])


def test_srt_after_video_fails(tmp_path, synthetic_video):
    root = release(tmp_path, synthetic_video)
    (root / "final/subtitles.srt").write_text(srt(end="00:00:03,000"))
    result = validator.validate(root)
    assert result["status"] == "FAIL"
    assert "beyond media" in str(result["errors"])


@pytest.mark.parametrize("filename,value", [("episode_manifest.json", []), ("final/content_audit.json", None),
    ("source_manifest.json", []), ("content_manifest.json", []), ("selection_report.json", [])])
def test_nonobject_json_is_failure_not_traceback(tmp_path, synthetic_video, filename, value):
    root = release(tmp_path, synthetic_video, version=3)
    put(root, filename, value)
    assert validator.validate(root)["status"] == "FAIL"


def test_corrupt_json_replaces_old_cli_pass_report(tmp_path, synthetic_video):
    root = release(tmp_path, synthetic_video)
    (root / "episode_manifest.json").write_text("{broken")
    output = root / "validation_result.json"
    output.write_text('{"status":"PASS"}')
    run = subprocess.run([sys.executable, str(SCRIPTS / "validate_episode.py"), str(root),
                          "--json-output", str(output)], capture_output=True, text=True)
    assert run.returncode == 1
    assert json.loads(run.stdout)["status"] == "FAIL"
    assert json.loads(output.read_text())["status"] == "FAIL"
    assert "Traceback" not in run.stderr


def test_research_failure_automatically_blocks_before_media_tools(tmp_path, synthetic_video, monkeypatch):
    root = release(tmp_path, synthetic_video, version=3)
    sources = json.loads((root / "source_manifest.json").read_text())
    sources["sources"][0]["read_status"] = "unread"
    put(root, "source_manifest.json", sources)
    monkeypatch.setattr(validator, "probe", lambda _: pytest.fail("must fail before media tools"))
    result = validator.validate(root)
    assert result["status"] == "FAIL"
    assert "not read" in str(result["errors"])


def test_explicit_research_required_on_legacy(tmp_path, synthetic_video):
    root = release(tmp_path, synthetic_video)
    assert validator.validate(root, require_research=True)["status"] == "FAIL"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.7, "bad"])
def test_nonfinite_or_wrong_episode_tail_fails(tmp_path, synthetic_video, value):
    root = release(tmp_path, synthetic_video)
    put(root, "episode_manifest.json", {"tail_hold_sec": value})
    assert validator.validate(root)["status"] == "FAIL"


def test_ffprobe_failure_returns_failure_report(tmp_path, synthetic_video, monkeypatch):
    root = release(tmp_path, synthetic_video)
    def failed(_):
        raise subprocess.CalledProcessError(1, "ffprobe")
    monkeypatch.setattr(validator, "probe", failed)
    assert validator.validate(root)["status"] == "FAIL"


def test_partial_ffmpeg_logs_on_nonzero_exit_do_not_prove_tail(monkeypatch):
    monkeypatch.setattr(validator.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        [], 1, "", "silence_start: 1.2\nsilence_end: 2.0"))
    assert validator.trailing_silence(Path("synthetic"), 2.0) is None


@pytest.mark.parametrize("logs", ["silence_start: 1.2\nsilence_end: 3.0", "silence_start: 3\nsilence_end: 2",
    "silence_start: 0.2\nsilence_end: 1.0", "", "silence_start: 1.2"])
def test_invalid_silence_boundaries_are_not_measurements(monkeypatch, logs):
    monkeypatch.setattr(validator.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, "", logs))
    assert validator.trailing_silence(Path("synthetic"), 2.0) is None


def test_advisory_warnings_do_not_block_release(tmp_path, synthetic_video, monkeypatch):
    root = release(tmp_path, synthetic_video)
    monkeypatch.setattr(validator, "scan_episode", lambda _: {"status": "WARNING", "images_and_frames_scanned": 1,
        "warnings": [{"asset": "test.png", "type": "synthetic marker", "confidence": "low"}]})
    result = validator.validate(root)
    assert result["status"] == "PASS", result
    assert result["checks"]["visual_risk_scan"]["blocking"] is False
    assert (root / "final/visual_risk_report.json").exists()


def test_preflight_runs_without_any_site_packages(tmp_path):
    root = project(tmp_path)
    run = subprocess.run([sys.executable, "-S", str(SCRIPTS / "preflight_episode.py"), str(root)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "STRUCTURE_OK"


def test_validator_import_does_not_require_visual_packages(tmp_path):
    run = subprocess.run([sys.executable, "-S", str(SCRIPTS / "validate_episode.py"), str(tmp_path)], capture_output=True, text=True)
    assert run.returncode == 1
    assert json.loads(run.stdout)["status"] == "FAIL"
    assert "Traceback" not in run.stderr
