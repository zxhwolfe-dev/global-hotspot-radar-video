from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_episode as renderer  # noqa: E402
from ghr_renderer.compositor import composite, load_premultiplied, transform_layer  # noqa: E402
from ghr_renderer.contracts import safe_resolve_asset  # noqa: E402
from ghr_renderer.motion import action_window, ease, layer_motion_end, scene_v2_state  # noqa: E402
from ghr_renderer.subtitles import write_single_box_ass  # noqa: E402


def scene_layer() -> dict:
    return {
        "asset": "transparent/subject.png",
        "role": "subject",
        "position": [0.5, 0.6],
        "anchor": [0.5, 0.9],
        "width_ratio": 0.5,
        "actions": [{
            "preset": "subject_reveal",
            "start": "card_start+0.2",
            "anticipation_sec": 0.1,
            "duration_sec": 0.8,
            "settle_sec": 0.3,
            "easing": "out_cubic",
        }],
    }


def test_scene_v2_has_distinct_phases_and_freezes() -> None:
    layer = scene_layer()
    window = action_window(layer)
    assert window["end"] == pytest.approx(1.4)
    waiting = scene_v2_state(layer, 0.1, 4.0, 1080, 1920)
    action = scene_v2_state(layer, 0.7, 4.0, 1080, 1920)
    held = scene_v2_state(layer, 2.0, 4.0, 1080, 1920)
    much_later = scene_v2_state(layer, 3.8, 4.0, 1080, 1920)
    assert waiting["phase"] == "waiting" and waiting["opacity"] == 0.0
    assert action["phase"] == "action" and 0.0 < action["opacity"] <= 1.0
    assert held["phase"] == "hold" and held["stable"] == 1.0
    for key in ("x", "y", "scale_x", "scale_y", "rotation", "opacity", "blur"):
        assert held[key] == pytest.approx(much_later[key])


def test_easing_curves_are_not_one_shared_curve() -> None:
    midpoint = 0.5
    values = {name: ease(name, midpoint) for name in ("linear", "out_cubic", "out_quint", "in_cubic", "in_out_sine")}
    assert len({round(value, 5) for value in values.values()}) >= 4
    assert values["in_cubic"] < values["linear"] < values["out_cubic"] < values["out_quint"]


def test_scene_v2_motion_end_is_finite() -> None:
    assert layer_motion_end(scene_layer(), engine="scene_v2") == pytest.approx(1.4)
    legacy = {"start_sec": 0.2, "reveal_sec": 0.6, "drift": [0.01, 0.0]}
    assert layer_motion_end(legacy, engine="legacy") == float("inf")


def test_premultiplied_transform_avoids_dark_transparent_fringe(tmp_path: Path) -> None:
    rgba = np.zeros((3, 3, 4), dtype=np.uint8)
    rgba[1, 1] = [255, 30, 20, 255]
    rgba[0, 0] = [0, 0, 0, 0]
    path = tmp_path / "subject.png"
    Image.fromarray(rgba, "RGBA").save(path)
    source = load_premultiplied(path)
    transformed = transform_layer(
        source,
        target_width=30,
        scale_x=1.0,
        scale_y=1.0,
        rotation=7.0,
        blur=0.0,
    )
    base = np.full((48, 48, 3), 245, dtype=np.uint8)
    composite(base, transformed, 24, 24, 1.0)
    changed = base[np.any(base != 245, axis=2)]
    assert changed.size
    assert np.all(changed[:, 0] >= changed[:, 1])
    assert np.all(changed[:, 0] >= changed[:, 2])


def test_safe_resolve_asset_rejects_project_escape(tmp_path: Path) -> None:
    inside = tmp_path / "inside.png"
    inside.write_bytes(b"x")
    assert safe_resolve_asset(tmp_path, "inside.png", label="test") == inside.resolve()
    with pytest.raises(ValueError, match="escapes"):
        safe_resolve_asset(tmp_path, "../outside.png", label="test")


def test_audio_fingerprint_changes_with_text_and_source_audio(tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"first")
    data = {
        "language": "zh-CN",
        "voice": {"model": "qwen-audio-3.0-tts-plus", "voice": "longanlingxin"},
        "video": {"pre_roll_seconds": 0.35, "tail_hold_seconds": 0.8},
        "audio": {"speech_gap_seconds": 0.24},
        "cards": [
            {"id": "cover"},
            {
                "id": "story",
                "tts_text": "[serious]第一版口播。",
                "caption_text": "第一版口播。",
                "original_clip": {
                    "audio": "clip.mp4",
                    "source_start_sec": 1.0,
                    "duration_sec": 2.0,
                },
            },
        ],
    }
    original = renderer.audio_fingerprint(tmp_path, data)
    changed_text = copy.deepcopy(data)
    changed_text["cards"][1]["tts_text"] = "[serious]第二版口播。"
    changed_text["cards"][1]["caption_text"] = "第二版口播。"
    assert renderer.audio_fingerprint(tmp_path, changed_text) != original
    source.write_bytes(b"second and longer")
    assert renderer.audio_fingerprint(tmp_path, data) != original


def test_opening_transition_is_not_collapsed_by_short_cover() -> None:
    data = {
        "video": {
            "fps": 30,
            "same_story_transition": "fade",
            "same_story_transition_seconds": 0.24,
            "cross_story_transitions": ["smoothleft"],
            "cross_story_transition_seconds": 0.36,
        },
        "cards": [{"id": "cover"}, {"id": "first"}],
    }
    timeline = [
        {"id": "cover", "story_id": "intro", "duration_sec": 0.35, "end_sec": 0.35},
        {"id": "first", "story_id": "story-1", "duration_sec": 8.0, "end_sec": 8.35},
    ]
    plan = renderer.transition_plan(data, timeline)
    assert plan[0]["duration_sec"] == pytest.approx(0.36)


def test_scene_v2_tail_motion_is_release_blocking() -> None:
    data = {
        "video": {"fps": 30, "tail_hold_seconds": 0.8},
        "cards": [
            {"id": "cover"},
            {
                "id": "last",
                "animation": {
                    "engine": "scene_v2",
                    "layers": [{
                        **scene_layer(),
                        "actions": [{
                            "preset": "subject_reveal",
                            "start": 2.0,
                            "anticipation_sec": 0.1,
                            "duration_sec": 0.8,
                            "settle_sec": 0.3,
                        }],
                    }],
                },
            },
        ],
    }
    timeline = [
        {"id": "cover", "start_sec": 0.0, "end_sec": 0.35, "duration_sec": 0.35},
        {"id": "last", "start_sec": 0.35, "spoken_end_sec": 3.0, "end_sec": 3.8, "duration_sec": 3.45},
    ]
    report = renderer.analyze_motion_plan(data, timeline)
    assert report["status"] == "FAIL"
    assert any("final 0.8-second tail" in error for error in report["errors"])


def test_scene_v2_layered_video_smoke(tmp_path: Path) -> None:
    background = tmp_path / "background.png"
    Image.new("RGB", (180, 320), (28, 45, 64)).save(background)
    transparent = tmp_path / "subject.png"
    subject = Image.new("RGBA", (90, 130), (0, 0, 0, 0))
    subject.paste((230, 90, 48, 255), (12, 12, 78, 125))
    subject.save(transparent)
    output = tmp_path / "layered.mp4"
    profile = renderer.RenderProfile("test", 180, 320, 12, "ultrafast", 28)
    renderer.layered_video(
        background,
        {
            "engine": "scene_v2",
            "layers": [{
                **scene_layer(),
                "asset": "subject.png",
                "shadow": {"type": "contact", "offset": [4, 7], "blur": 8, "opacity": 0.3},
            }],
        },
        2.0,
        output,
        root=tmp_path,
        profile=profile,
    )
    assert output.is_file() and renderer.duration(output) >= 1.9


def test_scene_window_source_video_smoke(tmp_path: Path) -> None:
    backdrop = tmp_path / "backdrop.png"
    Image.new("RGB", (180, 320), (34, 28, 48)).save(backdrop)
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=160x90:rate=12:duration=1.2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source),
        ],
        check=True,
    )
    output = tmp_path / "window.mp4"
    profile = renderer.RenderProfile("test", 180, 320, 12, "ultrafast", 28)
    renderer.source_video(
        source,
        0.0,
        1.0,
        output,
        profile=profile,
        presentation={
            "mode": "scene_window",
            "foreground_width_ratio": 0.84,
            "foreground_height": 110,
            "vertical_position": 0.48,
            "video_fade_in_sec": 0.12,
            "video_fade_out_sec": 0.14,
        },
        backdrop=backdrop,
    )
    assert output.is_file() and renderer.duration(output) >= 0.9


def test_source_window_height_is_profile_invariant() -> None:
    full = renderer.resolve_foreground_height(
        {"foreground_height_ratio": 0.375},
        output_height=1920,
        reference_height=1920,
        default_logical_height=720,
        minimum=96,
    )
    preview = renderer.resolve_foreground_height(
        {"foreground_height_ratio": 0.375},
        output_height=960,
        reference_height=1920,
        default_logical_height=720,
        minimum=48,
    )
    legacy_preview = renderer.resolve_foreground_height(
        {"foreground_height": 720},
        output_height=960,
        reference_height=1920,
        default_logical_height=720,
        minimum=48,
    )
    assert full == 720
    assert preview == 360
    assert legacy_preview == preview


def test_subtitle_ass_uses_one_vector_box_per_cue(tmp_path: Path) -> None:
    output = tmp_path / "subtitles.ass"
    cues = [{"start_sec": 0.4, "end_sec": 2.8, "text": "第一行\n第二行"}]
    write_single_box_ass(
        cues,
        output,
        width=1080,
        height=1920,
        font_name="Microsoft YaHei",
        font_size=58,
        margin_h=82,
        margin_v=250,
        wrapped_texts=["第一行\n第二行"],
    )
    content = output.read_text(encoding="utf-8")
    assert content.count("Dialogue: 0") == 1
    assert content.count("Dialogue: 1") == 1
    assert content.count("\\p1") == 1
    assert "第一行\\N第二行" in content


def test_caption_chunks_create_multiple_pages_inside_one_spoken_card() -> None:
    card = {
        "id": "story",
        "caption_text": "第一句保留事实。第二句解释意义。最后一句提出问题？",
        "caption_chunks": ["第一句保留事实。", "第二句解释意义。", "最后一句提出问题？"],
    }
    cues = renderer.build_caption_cues(card, 2.0, 8.0)
    assert [cue["text"] for cue in cues] == card["caption_chunks"]
    assert cues[0]["start_sec"] == pytest.approx(2.0)
    assert cues[-1]["end_sec"] == pytest.approx(8.0)
    assert all(left["end_sec"] == pytest.approx(right["start_sec"]) for left, right in zip(cues, cues[1:]))
