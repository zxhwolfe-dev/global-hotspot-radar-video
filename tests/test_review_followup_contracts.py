"""Offline counterexamples for caption boundaries and master-audio reuse."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ghr_renderer import audio_contracts as audio
from ghr_renderer.subtitle_validation import check_srt, expected_pages


def manifest(text="It is now here."):
    return {
        "language": "en", "quality_contract_version": 3,
        "video": {"tail_hold_seconds": 0.8},
        "cards": [{"id": "cover"}, {"id": "one", "story_id": "story-a",
                   "tts_text": text, "caption_text": text}],
    }


def srt(text):
    return f"1\n00:00:00,350 --> 00:00:02,000\n{text}\n"


@pytest.mark.parametrize("original,changed", [
    ("It is now here.", "It is nowhere."),
    ("She is not able.", "She is notable."),
    ("They must re sign.", "They must resign."),
    ("Use 1 234 units.", "Use 1234 units."),
    ("Try café noir.", "Try cafénoir."),
])
def test_english_word_boundaries_are_not_discarded(original, changed):
    data = manifest(original)
    assert check_srt(srt(changed), data)["errors"]
    data["cards"][1]["caption_text"] = changed
    assert expected_pages(data)[1]


@pytest.mark.parametrize("original,wrapped", [
    ("It is now here.", "It is now\nhere."),
    ("Use OpenAI today.", "Use OpenAI\ntoday."),
    ("Hello, world.", "Hello,\nworld."),
    ("Hello,world.", "Hello,\nworld."),
    ("你好世界。", "你好\n世界。"),
    ("中文介绍OpenAI工具。", "中文介绍\nOpenAI工具。"),
    ("Try café noir.", "Try cafe\u0301\nnoir."),
])
def test_wrapping_preserves_real_words(original, wrapped):
    assert not check_srt(srt(wrapped), manifest(original))["errors"]


def test_unlisted_english_word_cannot_be_broken_into_two_words():
    assert check_srt(srt("Use Open\nAI today."), manifest("Use OpenAI today."))["errors"]


def test_english_caption_pages_preserve_word_boundaries():
    data = manifest()
    data["cards"][1]["caption_chunks"] = ["It is now", "here."]
    assert not expected_pages(data)[1]
    data["cards"][1].update(tts_text="It is nowhere.", caption_text="It is nowhere.")
    assert expected_pages(data)[1]


def test_chinese_caption_pages_remain_compatible():
    data = manifest("第一句。第二句。")
    data["cards"][1]["caption_chunks"] = ["第一句。", "第二句。"]
    assert not expected_pages(data)[1]


@pytest.mark.parametrize("original,changed", [("3.4%", "34%"), ("3.4%", "3.4"), ("US", "us")])
def test_punctuation_and_case_remain_protected(original, changed):
    assert check_srt(srt(changed), manifest(original))["errors"]


def with_source(tmp_path):
    data = manifest()
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"first")
    data["cards"][1]["original_clip"] = {"audio": "clip.mp4", "source_start_sec": 0, "duration_sec": 1}
    return data, source


@pytest.mark.parametrize("index,value", [(0, "new-intro"), (1, "story-b"), (1, "intro")])
def test_story_reassignment_invalidates_master_not_provider_tts(tmp_path, index, value):
    data = manifest()
    old = audio.audio_fingerprint(tmp_path, data)
    tts = audio.tts_fingerprint(data)
    data["cards"][index]["story_id"] = value
    assert audio.audio_fingerprint(tmp_path, data) != old
    assert audio.tts_fingerprint(data) == tts


def test_same_size_same_mtime_source_edit_is_not_reused(tmp_path):
    data, source = with_source(tmp_path)
    before = source.stat()
    old = audio.audio_fingerprint(tmp_path, data)
    source.write_bytes(b"other")
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert source.stat().st_size == before.st_size
    assert source.stat().st_mtime_ns == before.st_mtime_ns
    assert audio.audio_fingerprint(tmp_path, data) != old


def test_unchanged_bytes_with_new_mtime_do_not_invalidate_audio(tmp_path):
    data, source = with_source(tmp_path)
    old = audio.audio_fingerprint(tmp_path, data)
    before = source.stat()
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    assert audio.audio_fingerprint(tmp_path, data) == old


def test_copy_of_same_project_has_portable_content_fingerprint(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(); second.mkdir()
    data, source = with_source(first)
    shutil.copyfile(source, second / "clip.mp4")
    assert audio.audio_fingerprint(first, data) == audio.audio_fingerprint(second, data)


def test_repeated_source_is_streamed_once_per_fingerprint_call(tmp_path, monkeypatch):
    data, source = with_source(tmp_path)
    other = copy.deepcopy(data["cards"][1]); other["id"] = "two"
    data["cards"].append(other)
    calls = []
    real = audio.sha256_file
    def record(path):
        calls.append(path)
        return real(path)
    monkeypatch.setattr(audio, "sha256_file", record)
    audio.audio_fingerprint(tmp_path, data)
    assert calls == [source.resolve()]
    audio.audio_fingerprint(tmp_path, data)
    assert len(calls) == 2  # no stale process-global metadata cache


def test_change_during_source_hash_is_rejected(tmp_path, monkeypatch):
    data, source = with_source(tmp_path)
    def replace_during_read(path):
        path.write_bytes(b"changed during hash")
        return "0" * 64
    monkeypatch.setattr(audio, "sha256_file", replace_during_read)
    with pytest.raises(RuntimeError, match="changed"):
        audio.audio_fingerprint(tmp_path, data)


def test_audio_change_does_not_force_new_provider_tts(tmp_path):
    data, source = with_source(tmp_path)
    old = audio.tts_fingerprint(data)
    source.write_bytes(b"replacement")
    assert audio.tts_fingerprint(data) == old


def test_visual_only_changes_do_not_invalidate_audio(tmp_path):
    data = manifest()
    old = audio.audio_fingerprint(tmp_path, data)
    data["cards"][1]["image"] = "different.png"
    data["cards"][1]["animation"] = {"engine": "scene_v2"}
    assert audio.audio_fingerprint(tmp_path, data) == old


def test_default_story_ids_match_renderer_defaults(tmp_path):
    data = manifest()
    del data["cards"][1]["story_id"]
    old = audio.audio_fingerprint(tmp_path, data)
    data["cards"][0]["story_id"] = "intro"
    data["cards"][1]["story_id"] = "one"
    assert audio.audio_fingerprint(tmp_path, data) == old


def test_audio_asset_cannot_escape_project(tmp_path):
    data = manifest()
    data["cards"][1]["original_clip"] = {"audio": "../outside.wav"}
    with pytest.raises(ValueError, match="escapes"):
        audio.audio_fingerprint(tmp_path, data)


@pytest.mark.parametrize("original,wrapped", [("AB", "A\nB"), ("2026", "20\n2\n6")])
def test_legacy_line_count_exemption_does_not_erase_word_or_number_boundaries(original, wrapped):
    data = manifest(original)
    data["quality_contract_version"] = 1
    errors = check_srt(srt(wrapped), data)["errors"]
    assert any("text differs" in error for error in errors)
    assert not any("two subtitle lines" in error for error in errors)
