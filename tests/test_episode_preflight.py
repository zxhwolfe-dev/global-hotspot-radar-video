"""Offline synthetic fixtures. No news claims, credentials or paid providers."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from ghr_renderer.subtitle_validation import check_srt, expected_pages, parse_srt, split_terms
from preflight_episode import check_project


def manifest(text="2026", *, version=1):
    return {"quality_contract_version": version, "language": "zh-CN",
            "video": {"tail_hold_seconds": 0.8, "subtitle_keep_terms": ["三点四", "OpenAI"]},
            "cards": [{"id": "cover", "image": "asset.png"},
                      {"id": "news", "image": "asset.png", "purpose": "fact",
                       "tts_text": "[serious]" + text, "caption_text": text}]}


def put(root, name, value):
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def project(tmp_path, text="2026", *, version=1):
    # Preflight checks paths/existence, not image decoding. Deliberate fixture bytes.
    (tmp_path / "asset.png").write_bytes(b"synthetic image placeholder")
    put(tmp_path, "content_manifest.json", manifest(text, version=version))
    return tmp_path


def research(root, *, version=1):
    chosen = {key: "Synthetic test only" for key in
              ("topic", "today_delta", "audience_payoff", "primary_evidence", "visual_proof", "unknowns", "editorial_angle")}
    chosen.update(candidate_origin="web", target_market="test", viewer_question="test?", time_basis="synthetic",
                  claim_evidence=[{"claim": "Synthetic only", "kind": "fact", "source_ids": ["s1"]}])
    put(root, "selection_report.json", {"research_contract_version": version,
        "discovery_log": [{"route": "web", "query": "synthetic query", "status": "completed"}], "final_selection": [chosen]})
    put(root, "source_manifest.json", {"sources": [{"id": "s1", "url": "https://example.org/synthetic",
        "publisher": "Synthetic", "kind": "primary", "read_status": "full", "origin_group": "test",
        "fetched_at": "2026-09-18T01:00:00Z"}]})


def srt(text="2026", *, start="00:00:00,100", end="00:00:01,200", index=1):
    return f"{index}\n{start} --> {end}\n{text}\n"


def test_numeric_caption_is_not_counted_as_an_extra_index():
    result = check_srt(srt(), manifest())
    assert not result["errors"]
    assert result["cue_count"] == 1


def test_bom_crlf_and_layout_whitespace():
    text = "\ufeff" + srt("OpenAI\n变化。").replace("\n", "\r\n")
    assert not check_srt(text, manifest("OpenAI 变化。"))["errors"]


@pytest.mark.parametrize("text", ["", "1\n2026", "1\n00:60:00,000 --> 00:61:00,000\nx",
    "1\n00:00:00.000 --> 00:00:01.000\nx", "one\n00:00:00,000 --> 00:00:01,000\nx",
    "1\n00:00:00,000 --> 00:00:01,000", "1\n-00:00:01,000 --> 00:00:01,000\nx"])
def test_malformed_srt_returns_diagnostics(text):
    assert check_srt(text, manifest())["errors"]


@pytest.mark.parametrize("text", ["2027", "20.26", "2026%", "不是2026"])
def test_equal_cue_count_does_not_hide_changed_words(text):
    assert "differs" in str(check_srt(srt(text), manifest())["errors"])


@pytest.mark.parametrize("kwargs", [{"index": 2}, {"end": "00:00:00,100"}, {"end": "00:00:00,099"}])
def test_bad_sequence_or_nonpositive_duration(kwargs):
    assert check_srt(srt(**kwargs), manifest())["errors"]


def test_overlap_and_order_are_checked():
    data = manifest("AB")
    data["cards"][1]["caption_chunks"] = ["A", "B"]
    text = srt("A", end="00:00:00,800") + "\n" + srt("B", start="00:00:00,799", index=2)
    assert "overlaps" in str(check_srt(text, data)["errors"])
    text = text.replace("00:00:00,799", "00:00:00,800")
    assert not check_srt(text, data)["errors"]


def test_end_after_video_and_millisecond_rounding():
    assert "beyond" in str(check_srt(srt(), manifest(), duration_sec=1.1)["errors"])
    assert not check_srt(srt(), manifest(), duration_sec=1.1995)["errors"]


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), 0, -1])
def test_nonfinite_duration_fails(duration):
    assert check_srt(srt(), manifest(), duration_sec=duration)["errors"]


def test_two_lines_for_v2_and_v3_but_legacy_v1_preserved():
    text = srt("20\n2\n6")
    assert not check_srt(text, manifest(version=1))["errors"]
    assert check_srt(text, manifest(version=2))["errors"]
    assert check_srt(text, manifest(version=3))["errors"]


@pytest.mark.parametrize("parts,term", [(["三点", "四"], "三点四"), (["Open", "AI"], "OpenAI")])
def test_protected_terms_across_pages_and_lines(parts, term):
    data = manifest(term)
    data["cards"][1]["caption_chunks"] = parts
    assert "split across caption pages" in str(expected_pages(data)[1])
    data["cards"][1].pop("caption_chunks")
    assert "split across subtitle lines" in str(check_srt(srt("\n".join(parts)), data)["errors"])
    assert not check_srt(srt(term), data)["errors"]


def test_overlapping_and_repeated_protected_terms():
    assert split_terms(["三点四和三点", "四"], ["三点四", "点四"]) == ["三点四", "点四"]
    assert split_terms(["三点四", "和三点四"], ["三点四"]) == []


@pytest.mark.parametrize("replacement", ["34", "3,4", "3.4%"])
def test_caption_chunks_preserve_decimal_and_units(replacement):
    data = manifest("3.4")
    data["cards"][1]["caption_chunks"] = [replacement]
    assert "change caption text" in str(expected_pages(data)[1])


@pytest.mark.parametrize("caption", ["3.5", "3.4%", "34"])
def test_narration_caption_mismatch(caption):
    data = manifest("3.4")
    data["cards"][1]["caption_text"] = caption
    assert "differs from untagged" in str(expected_pages(data)[1])


@pytest.mark.parametrize("field,value", [("tts_text", 3), ("caption_text", None),
    ("caption_chunks", [None]), ("caption_chunks", []), ("caption_chunks", "bad"), ("caption_chunks", [""] )])
def test_malformed_caption_fields(field, value):
    data = manifest()
    data["cards"][1][field] = value
    assert expected_pages(data)[1]


def test_unknown_voice_tag():
    data = manifest()
    data["cards"][1]["tts_text"] = "[fake]2026"
    assert "unsupported TTS tag" in str(expected_pages(data)[1])


@pytest.mark.parametrize("value", [True, {}, "OpenAI", [None], [""]])
def test_bad_keep_terms_diagnosed(value):
    data = manifest()
    data["video"]["subtitle_keep_terms"] = value
    assert check_srt(srt(), data)["errors"]


def test_unicode_content_not_dropped():
    data = manifest("café 3.4")
    assert not check_srt(srt("cafe\u0301 3.4"), data)["errors"]
    assert check_srt(srt("cafe 3.4"), data)["errors"]


def test_preflight_without_side_effects_or_new_legacy_requirements(tmp_path):
    root = project(tmp_path)
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    result = check_project(root)
    assert result["status"] == "STRUCTURE_OK"
    assert result["checks"]["research"]["status"] == "NOT_CONFIGURED"
    assert result["publication_readiness"] == "not_assessed"
    assert before == {p.name: p.read_bytes() for p in root.iterdir()}


def test_new_episode_requires_research(tmp_path):
    root = project(tmp_path, version=3)
    assert check_project(root, require_research=True)["status"] == "FAIL"
    research(root)
    assert check_project(root, require_research=True)["status"] == "STRUCTURE_OK"


@pytest.mark.parametrize("value", [None, 2, True, "1"])
def test_explicit_invalid_research_version_never_downgraded_to_legacy(tmp_path, value):
    root = project(tmp_path)
    research(root, version=value)
    assert check_project(root)["status"] == "FAIL"


def test_changed_sources_rechecked_even_with_old_saved_pass(tmp_path):
    root = project(tmp_path)
    research(root)
    first = check_project(root)
    put(root, "preview/selection_research_check.json", first)
    sources = json.loads((root / "source_manifest.json").read_text())
    sources["sources"][0]["read_status"] = "headline"
    put(root, "source_manifest.json", sources)
    second = check_project(root)
    assert second["status"] == "FAIL"
    assert first["input_sha256"] != second["input_sha256"]


@pytest.mark.parametrize("value", [None, [], True, "hello"])
def test_non_object_manifest_fails(tmp_path, value):
    root = project(tmp_path)
    put(root, "content_manifest.json", value)
    assert check_project(root)["status"] == "FAIL"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.91, "bad"])
def test_invalid_tail_setting_not_silently_accepted(tmp_path, value):
    root = project(tmp_path)
    data = manifest()
    data["video"]["tail_hold_seconds"] = value
    put(root, "content_manifest.json", data)
    assert check_project(root)["status"] == "FAIL"


@pytest.mark.parametrize("bad_path", ["../outside.png", "/tmp/outside.png", "", "absent.png"])
def test_missing_or_escaping_assets_fail(tmp_path, bad_path):
    root = project(tmp_path)
    data = manifest()
    data["cards"][1]["image"] = bad_path
    put(root, "content_manifest.json", data)
    assert check_project(root)["status"] == "FAIL"


def test_symlink_escape_fails(tmp_path):
    root = project(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "_outside.png")
    outside.write_bytes(b"test")
    (root / "asset.png").unlink()
    (root / "asset.png").symlink_to(outside)
    assert check_project(root)["status"] == "FAIL"


def test_malformed_json_cli_is_structured_failure(tmp_path):
    (tmp_path / "content_manifest.json").write_text("{bad")
    run = subprocess.run([sys.executable, str(SCRIPTS / "preflight_episode.py"), str(tmp_path)], capture_output=True, text=True)
    assert run.returncode == 2
    assert json.loads(run.stdout)["status"] == "FAIL"
    assert "Traceback" not in run.stderr


def test_cli_gate_prevents_next_command_on_failure(tmp_path):
    root = project(tmp_path)
    marker = root / "renderer_must_not_start"
    # A local dummy process stands in for a costly next stage; it never executes.
    import shlex
    command = " ".join(map(shlex.quote, [sys.executable, str(SCRIPTS / "preflight_episode.py"), str(root), "--require-research"]))
    command += " && " + " ".join(map(shlex.quote, [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"]))
    run = subprocess.run(["bash", "-c", command], capture_output=True, text=True)
    assert run.returncode != 0
    assert not marker.exists()
