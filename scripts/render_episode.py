#!/usr/bin/env python3
"""Manifest-driven renderer for Global Hotspot Radar vertical episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from dotenv import load_dotenv
from PIL import Image, ImageStat

try:
    import jieba
    jieba.setLogLevel(30)
except ImportError:  # pragma: no cover - basic regex fallback remains available
    jieba = None

REPO = Path(os.environ.get("GHR_REPO_ROOT", "/home/zxhwolfe/project/akaiagents")).expanduser().resolve()
sys.path.insert(0, str(REPO))

from ai_douyin_video_pipeline.tts_provider import (
    AliyunQwenAudioTTS,
    AliyunTTSConfig,
)
from ghr_renderer.compositor import composite, load_premultiplied, shadow_layer, transform_layer
from ghr_renderer.audio_contracts import (
    audio_fingerprint,
    default_voice_instruction,
    tts_fingerprint,
    voice_identity,
)
from ghr_renderer.contracts import (
    safe_resolve_asset,
    sha256_file,
)
from ghr_renderer.motion import (
    LEGACY_EFFECTS,
    action_window,
    scene_v2_state,
)
from ghr_renderer.quality import analyze_motion_plan
from ghr_renderer.subtitles import write_single_box_ass

SUPPORTED_TAGS = {
    "curious", "excited", "serious", "sarcastic", "mischievously",
    "empathetic", "giggles", "laughing", "sighing", "clears throat",
    "whispers", "very fast", "very slowly",
}
TAG_RE = re.compile(r"\[([a-zA-Z ]+)\]")
RENDERER_CACHE_VERSION = "scene-v2-foundation-v1"
SUPPORTED_ANIMATION_ENGINES = {"legacy", "scene_v2"}
SUPPORTED_TRANSITIONS = {
    "fade", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "wipeleft", "wiperight", "slideleft", "slideright", "circleopen", "circleclose",
}


@dataclass(frozen=True)
class RenderProfile:
    name: str
    width: int
    height: int
    fps: int
    final_preset: str
    final_crf: int
    intermediate_preset: str = "ultrafast"
    intermediate_crf: int = 1
    boundary_checks: bool = True
    contact_sheet_frames: int = 16


def render_profile(data: dict[str, Any], mode: str) -> RenderProfile:
    cfg = data.get("video") or {}
    width = int(cfg.get("width", 1080))
    height = int(cfg.get("height", 1920))
    fps = int(cfg.get("fps", 30))
    if mode == "preview":
        preview_width = int(cfg.get("preview_width", 540))
        preview_height = max(2, round(height * preview_width / width / 2) * 2)
        return RenderProfile(mode, preview_width, preview_height, int(cfg.get("preview_fps", 24)), "ultrafast", 25, boundary_checks=False, contact_sheet_frames=9)
    if mode == "candidate":
        return RenderProfile(mode, width, height, fps, "veryfast", 20, contact_sheet_frames=12)
    return RenderProfile("release", width, height, fps, "medium", 17)


def run(*args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, capture_output=capture, text=True)


def probe(path: Path) -> dict[str, Any]:
    result = run(
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels,pix_fmt,color_space,color_transfer,color_primaries",
        "-of", "json", str(path), capture=True,
    )
    return json.loads(result.stdout)


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def normalized(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]+", "", text).lower()


def strip_tags(text: str) -> str:
    tags = TAG_RE.findall(text)
    unknown = sorted(set(tags) - SUPPORTED_TAGS)
    if unknown:
        raise ValueError(f"unsupported TTS tags: {unknown}")
    if len(re.findall(r"\[[^\]]+\]", text)) != len(tags):
        raise ValueError(f"invalid TTS tag syntax: {text}")
    return re.sub(r"\s+", " ", TAG_RE.sub("", text)).strip()


def srt_time(seconds: float) -> str:
    value = max(0, round(seconds * 1000))
    hours, value = divmod(value, 3_600_000)
    minutes, value = divmod(value, 60_000)
    secs, millis = divmod(value, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def wrap_srt_caption(text: str, max_units: int = 26, protected_terms: tuple[str, ...] = ()) -> str:
    """Wrap Chinese/mixed captions by visual width without changing their words."""
    def units(value: str) -> int:
        return sum(1 if ord(char) < 128 else 2 for char in value)

    source = text.strip()
    tokens: list[str] = []
    protected = tuple(sorted({term for term in protected_terms if term}, key=len, reverse=True))
    protected_re = re.compile("|".join(re.escape(term) for term in protected)) if protected else None

    def append_segment(segment: str) -> None:
        if not segment:
            return
        if jieba is not None:
            tokens.extend(token for token in jieba.cut(segment, cut_all=False) if token)
        else:
            tokens.extend(re.findall(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*|\s+|.", segment))

    cursor = 0
    for match in protected_re.finditer(source) if protected_re else ():
        append_segment(source[cursor:match.start()])
        tokens.append(match.group(0))
        cursor = match.end()
    append_segment(source[cursor:])
    closing_punctuation = set("，。；：！？、）》”’】〕…—,.!?;:")
    lines: list[str] = []
    current = ""
    for token in tokens:
        candidate = current + token
        if not current or units(candidate) <= max_units:
            current = candidate
            continue
        if token.strip() and all(char in closing_punctuation for char in token.strip()):
            current += token
            continue
        lines.append(current.rstrip())
        current = token.lstrip()
    if current:
        lines.append(current.rstrip())
    return "\n".join(lines)


def caption_chunks(card: dict[str, Any]) -> list[str]:
    """Return readable subtitle pages without changing the spoken card boundary."""
    configured = card.get("caption_chunks")
    if configured is None:
        return [str(card.get("caption_text") or "").strip()]
    if not isinstance(configured, list) or not configured:
        raise ValueError(f"caption_chunks on {card.get('id')} must be a non-empty list")
    chunks = [str(chunk).strip() for chunk in configured]
    if any(not chunk for chunk in chunks):
        raise ValueError(f"caption_chunks on {card.get('id')} must not contain blank text")
    return chunks


def caption_chunk_weight(text: str) -> float:
    """Approximate speech time while giving punctuation a small pause allowance."""
    base = sum(1.0 if ord(char) < 128 else 1.7 for char in text if not char.isspace())
    pause = sum(1.8 if char in "。！？.!?" else 0.8 if char in "，；：,;:" else 0.0 for char in text)
    return max(1.0, base + pause)


def build_caption_cues(card: dict[str, Any], start_sec: float, end_sec: float) -> list[dict[str, Any]]:
    """Distribute subtitle pages across one continuous provider TTS segment."""
    chunks = caption_chunks(card)
    if len(chunks) == 1:
        return [{"start_sec": round(start_sec, 3), "end_sec": round(end_sec, 3), "text": chunks[0]}]
    weights = [caption_chunk_weight(chunk) for chunk in chunks]
    total = sum(weights)
    span = max(0.0, end_sec - start_sec)
    cues: list[dict[str, Any]] = []
    cursor = start_sec
    cumulative = 0.0
    for index, (chunk, weight) in enumerate(zip(chunks, weights)):
        cumulative += weight
        boundary = end_sec if index == len(chunks) - 1 else start_sec + span * cumulative / total
        cues.append({"start_sec": round(cursor, 3), "end_sec": round(boundary, 3), "text": chunk})
        cursor = boundary
    return cues


def write_srt(cues: list[dict[str, Any]], path: Path, *, protected_terms: tuple[str, ...] = ()) -> None:
    blocks = [
        f"{index}\n{srt_time(cue['start_sec'])} --> {srt_time(cue['end_sec'])}\n"
        f"{wrap_srt_caption(str(cue['text']), protected_terms=protected_terms)}"
        for index, cue in enumerate(cues, 1)
    ]
    path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def make_silence(path: Path, seconds: float) -> None:
    run(
        "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
        "anullsrc=r=48000:cl=mono", "-t", f"{seconds:.3f}",
        "-c:a", "pcm_s16le", str(path),
    )


def audible_bounds(path: Path, *, threshold_db: float = -55.0) -> tuple[float, float]:
    """Return conservative speech bounds for a mono PCM WAV.

    Provider segment timings include variable leading/trailing silence. Detect the
    audible region, then preserve short guards so weak consonants and breaths are
    not clipped by a hard silence-removal filter.
    """
    with wave.open(str(path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        frame_count = source.getnframes()
        raw = source.readframes(frame_count)
    total_sec = frame_count / max(sample_rate, 1)
    if sample_width != 2 or not raw:
        return 0.0, total_sec
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32)
    if channels > 1:
        samples = samples[: len(samples) - (len(samples) % channels)].reshape(-1, channels).mean(axis=1)
    frame_samples = max(1, round(sample_rate * 0.01))
    padding = (-len(samples)) % frame_samples
    if padding:
        samples = np.pad(samples, (0, padding))
    framed = samples.reshape(-1, frame_samples)
    rms = np.sqrt(np.mean(np.square(framed), axis=1) + 1e-9)
    peak = float(rms.max(initial=0.0))
    if peak <= 0.0:
        return 0.0, total_sec
    absolute_floor = 32768.0 * (10.0 ** (threshold_db / 20.0))
    relative_floor = peak * (10.0 ** (-42.0 / 20.0))
    active = np.flatnonzero(rms >= max(absolute_floor, relative_floor))
    if not len(active):
        return 0.0, total_sec
    start = max(0.0, active[0] * frame_samples / sample_rate - 0.025)
    end = min(total_sec, (active[-1] + 1) * frame_samples / sample_rate + 0.085)
    if end <= start:
        return 0.0, total_sec
    return start, end


def validate_manifest(root: Path, data: dict[str, Any]) -> None:
    cards = data.get("cards")
    if not isinstance(cards, list) or len(cards) < 2:
        raise ValueError("content_manifest cards must include a silent cover and at least one spoken card")
    if str(cards[0].get("tts_text") or "").strip():
        raise ValueError("first card must be silent")
    silent_after_cover = [card.get("id") for card in cards[1:] if not str(card.get("tts_text") or "").strip()]
    if silent_after_cover:
        raise ValueError(f"only the first card may be silent: {silent_after_cover}")
    ids = [str(card.get("id") or "") for card in cards]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError("each card needs a unique non-empty id")
    if abs(float(data.get("video", {}).get("tail_hold_seconds", -1)) - 0.8) > 0.001:
        raise ValueError("Global Hotspot Radar tail_hold_seconds must be exactly 0.8")
    video = data.get("video") or {}
    for key, default in (("width", 1080), ("height", 1920), ("fps", 30)):
        if int(video.get(key, default)) <= 0:
            raise ValueError(f"video.{key} must be positive")
    transition_names = [
        video.get("same_story_transition", "fade"),
        *(video.get("cross_story_transitions") or ["smoothleft", "smoothright"]),
    ]
    for card in cards:
        override = card.get("transition_to_next")
        if isinstance(override, dict):
            transition_names.append(override.get("type") or override.get("transition") or "fade")
    unknown_transitions = sorted({str(name) for name in transition_names if str(name) not in SUPPORTED_TRANSITIONS})
    if unknown_transitions:
        raise ValueError(f"unsupported video transitions: {unknown_transitions}")
    protected_terms = tuple(str(term) for term in (video.get("subtitle_keep_terms") or []))
    quality_contract_version = int(data.get("quality_contract_version", 1))
    for card in cards:
        if str(card.get("tts_text") or "").strip():
            chunks = caption_chunks(card)
            if normalized("".join(chunks)) != normalized(str(card.get("caption_text") or "")):
                raise ValueError(f"caption_chunks do not reconstruct caption_text on {card['id']}")
            if quality_contract_version >= 2:
                oversized = [chunk for chunk in chunks if len(wrap_srt_caption(chunk, protected_terms=protected_terms).splitlines()) > 2]
                if oversized:
                    raise ValueError(f"quality contract v2 caption page exceeds two lines on {card['id']}: {oversized[0]}")
        image = safe_resolve_asset(root, card.get("image"), label=f"card image on {card['id']}")
        if not image.is_file():
            raise FileNotFoundError(f"missing card image: {image}")
        animation = card.get("animation")
        if animation:
            if not isinstance(animation, dict):
                raise ValueError(f"animation on {card['id']} must be an object")
            engine = str(animation.get("engine") or "legacy")
            if engine not in SUPPORTED_ANIMATION_ENGINES:
                raise ValueError(f"unsupported animation engine on {card['id']}: {engine}")
            layers = animation.get("layers") or []
            if not isinstance(layers, list) or not layers:
                raise ValueError(f"animation on {card['id']} needs at least one transparent layer")
            for layer in layers:
                if not isinstance(layer, dict) or not layer.get("asset"):
                    raise ValueError(f"animation layer on {card['id']} needs an asset")
                asset = safe_resolve_asset(root, layer["asset"], label=f"animation layer on {card['id']}")
                if not asset.is_file():
                    raise FileNotFoundError(f"missing animation layer: {asset}")
                if asset.suffix.casefold() != ".png":
                    raise ValueError(f"animation layers must be transparent PNG files: {asset}")
                with Image.open(asset) as layer_image:
                    if layer_image.mode not in {"RGBA", "LA"} and "transparency" not in layer_image.info:
                        raise ValueError(f"animation layer has no alpha channel: {asset}")
                    alpha = layer_image.convert("RGBA").getchannel("A")
                    alpha_min, alpha_max = alpha.getextrema()
                    if alpha_max <= 1:
                        raise ValueError(f"animation layer is fully transparent: {asset}")
                    if engine == "scene_v2" and alpha_min >= 254:
                        raise ValueError(f"scene_v2 layer must contain real transparency: {asset}")
                if not math.isfinite(float(layer.get("z", 0))):
                    raise ValueError(f"animation layer z must be finite on {card['id']}")
                if engine == "scene_v2":
                    action_window(layer)
                else:
                    effect = str(layer.get("effect") or "focus_in")
                    if effect not in LEGACY_EFFECTS:
                        raise ValueError(f"unsupported legacy animation effect on {card['id']}: {effect}")
        clip = card.get("original_clip")
        if not clip:
            continue
        for key in ("video", "audio", "source_start_sec", "duration_sec", "label"):
            if key not in clip:
                raise ValueError(f"original_clip on {card['id']} is missing {key}")
        for key in ("video", "audio"):
            clip_path = safe_resolve_asset(root, clip[key], label=f"original_clip {key} on {card['id']}")
            if not clip_path.is_file():
                raise FileNotFoundError(f"missing original clip {key}: {clip_path}")
        if float(clip["duration_sec"]) <= 0:
            raise ValueError(f"original_clip duration on {card['id']} must be positive")
        if float(clip["source_start_sec"]) < 0:
            raise ValueError(f"original_clip source_start_sec on {card['id']} must not be negative")
        source_end = float(clip["source_start_sec"]) + float(clip["duration_sec"])
        for key in ("video", "audio"):
            clip_path = safe_resolve_asset(root, clip[key], label=f"original_clip {key} on {card['id']}")
            if source_end > duration(clip_path) + 0.08:
                raise ValueError(f"original_clip {key} range exceeds source duration on {card['id']}")


def load_tts_config(data: dict[str, Any]) -> AliyunTTSConfig:
    load_dotenv(REPO / ".env")
    if not os.getenv("DASHSCOPE_API_KEY"):
        fallback = os.getenv("AI_DOUYIN_ALIYUN_KEY") or os.getenv("QWEN_API_KEY")
        if fallback:
            os.environ["DASHSCOPE_API_KEY"] = fallback
    base = AliyunTTSConfig.from_env()
    voice = data.get("voice") or {}
    instruction = str(voice.get("instruction") or default_voice_instruction(str(data.get("language") or "zh-CN")))
    return AliyunTTSConfig(
        api_key=base.api_key,
        model=str(voice.get("model") or base.model),
        voice=str(voice.get("voice") or "longanlingxin"),
        instruction=instruction,
        endpoint=base.endpoint,
        sample_rate=base.sample_rate,
        timeout_sec=base.timeout_sec,
        max_workers=1,
    )


def build_audio(root: Path, data: dict[str, Any], *, reuse_tts: bool = False) -> tuple[list[dict[str, Any]], float, float]:
    audio_dir, final_dir = root / "audio", root / "final"
    work = root / "temp" / "audio"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)
    pre_roll = float(data["video"].get("pre_roll_seconds", 0.35))
    tail = float(data["video"]["tail_hold_seconds"])
    audio_design = data.get("audio") or {}
    default_speech_gap = max(0.0, float(audio_design.get("speech_gap_seconds", 0.24)))
    speech_cards = data["cards"][1:]
    for card in speech_cards:
        plain = strip_tags(str(card["tts_text"]))
        if normalized(plain) != normalized(str(card.get("caption_text") or "")):
            raise ValueError(f"caption mismatch: {card['id']}")

    config = load_tts_config(data)
    combined = audio_dir / "narration_combined.mp3"
    timings_path = audio_dir / "line_timings.json"
    tts_meta_path = audio_dir / "tts_cache_meta.json"
    expected_tts_fingerprint = tts_fingerprint(data)
    if reuse_tts:
        if not combined.is_file() or not timings_path.is_file() or not tts_meta_path.is_file():
            raise FileNotFoundError("--reuse-tts requires narration_combined.mp3, line_timings.json, and tts_cache_meta.json")
        saved_meta = json.loads(tts_meta_path.read_text(encoding="utf-8"))
        if saved_meta.get("tts_fingerprint") != expected_tts_fingerprint:
            raise ValueError("saved TTS does not match the current text, captions, model, voice, or instruction")
        saved_rows = json.loads(timings_path.read_text(encoding="utf-8"))
        if [row.get("card_id") for row in saved_rows] != [card["id"] for card in speech_cards]:
            raise ValueError("saved TTS card IDs do not match the current manifest")
        provider_timings = [
            {"offset_sec": row["provider_offset_sec"], "end_sec": row["provider_end_sec"]}
            for row in saved_rows
        ]
    else:
        provider_timings = AliyunQwenAudioTTS(config).synthesize(
            "\n".join(str(card["tts_text"]) for card in speech_cards), combined
        )
        tts_meta_path.write_text(json.dumps({
            "contract": "global-hotspot-tts-v2",
            "tts_fingerprint": expected_tts_fingerprint,
            "voice": voice_identity(data),
            "card_ids": [card["id"] for card in speech_cards],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if len(provider_timings) != len(speech_cards):
        raise RuntimeError(f"TTS timing mismatch: {len(provider_timings)} != {len(speech_cards)}")

    concat_parts: list[Path] = []
    pre_silence = work / "pre_roll.wav"
    make_silence(pre_silence, pre_roll)
    concat_parts.append(pre_silence)
    current = pre_roll
    rows: list[dict[str, Any]] = []
    cues: list[dict[str, Any]] = []
    source_windows: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = [{
        "id": data["cards"][0]["id"],
        "story_id": data["cards"][0].get("story_id", "intro"),
        "image": data["cards"][0]["image"],
        "start_sec": 0.0,
        "end_sec": round(pre_roll, 3),
        "duration_sec": round(pre_roll, 3),
        "subtitle_cue_count": 0,
    }]

    for index, (card, timing) in enumerate(zip(speech_cards, provider_timings), 1):
        provider_start = float(timing["offset_sec"])
        provider_end = float(timing["end_sec"])
        provider_wav = work / f"provider_{index:02d}.wav"
        line_wav = work / f"line_{index:02d}.wav"
        run(
            "ffmpeg", "-y", "-v", "error", "-i", str(combined),
            "-af", f"atrim=start={provider_start:.6f}:end={provider_end:.6f},asetpts=PTS-STARTPTS",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(provider_wav),
        )
        audible_start, audible_end = audible_bounds(provider_wav)
        run(
            "ffmpeg", "-y", "-v", "error", "-i", str(provider_wav),
            "-af", f"atrim=start={audible_start:.6f}:end={audible_end:.6f},asetpts=PTS-STARTPTS",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(line_wav),
        )
        actual_spoken = duration(line_wav)
        card_start = current
        spoken_end = card_start + actual_spoken
        concat_parts.append(line_wav)
        clip = card.get("original_clip")
        clip_sec = float(clip["duration_sec"]) if clip else 0.0
        if clip:
            gap = work / f"source_gap_{index:02d}.wav"
            make_silence(gap, clip_sec)
            concat_parts.append(gap)
            source_windows.append({
                "card_id": card["id"],
                "start_sec": round(spoken_end, 3),
                "end_sec": round(spoken_end + clip_sec, 3),
                "duration_sec": clip_sec,
                "source_start_sec": float(clip["source_start_sec"]),
                "audio": clip["audio"],
                "video": clip["video"],
                "label": clip["label"],
                "audio_volume": float(clip.get("audio_volume", 0.78)),
                "audio_fade_in_sec": float(clip.get("audio_fade_in_sec", 0.08)),
                "audio_fade_out_sec": float(clip.get("audio_fade_out_sec", 0.14)),
            })
        pause_after = 0.0 if index == len(speech_cards) else max(
            0.0, float(card.get("pause_after_seconds", default_speech_gap))
        )
        if pause_after:
            pause_path = work / f"pause_{index:02d}.wav"
            make_silence(pause_path, pause_after)
            concat_parts.append(pause_path)
        card_end = spoken_end + clip_sec + pause_after
        card_cues = build_caption_cues(card, card_start, spoken_end)
        cues.extend(card_cues)
        rows.append({
            "card_id": card["id"], "tts_text": card["tts_text"],
            "plain_text": strip_tags(str(card["tts_text"])), "caption_text": card["caption_text"],
            "provider_offset_sec": provider_start, "provider_end_sec": provider_end,
            "audible_trim_start_sec": round(audible_start, 3),
            "audible_trim_end_sec": round(audible_end, 3),
            "start_sec": round(card_start, 3), "spoken_end_sec": round(spoken_end, 3),
            "end_sec": round(card_end, 3), "spoken_duration_sec": round(actual_spoken, 3),
            "original_clip_duration_sec": clip_sec, "pause_after_sec": round(pause_after, 3),
            "caption_chunks": [cue["text"] for cue in card_cues],
        })
        timeline.append({
            "id": card["id"], "story_id": card.get("story_id", card["id"]), "image": card["image"],
            "start_sec": round(card_start, 3), "spoken_end_sec": round(spoken_end, 3),
            "end_sec": round(card_end, 3), "duration_sec": round(card_end - card_start, 3),
            "subtitle_cue_count": len(card_cues),
        })
        current = card_end

    tail_silence = work / "tail.wav"
    make_silence(tail_silence, tail)
    concat_parts.append(tail_silence)
    timeline[-1]["end_sec"] = round(current + tail, 3)
    timeline[-1]["duration_sec"] = round(timeline[-1]["end_sec"] - timeline[-1]["start_sec"], 3)
    total_sec = current + tail
    concat_list = work / "concat.txt"
    concat_list.write_text("".join(f"file '{part}'\n" for part in concat_parts), encoding="utf-8")
    narration_raw = audio_dir / "narration_with_windows.wav"
    narration_master = audio_dir / "narration_loudnorm.wav"
    run(
        "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(narration_raw),
    )
    run(
        "ffmpeg", "-y", "-v", "error", "-i", str(narration_raw),
        "-af", "loudnorm=I=-16:TP=-1.8:LRA=7", "-ar", "48000", "-ac", "1",
        "-c:a", "pcm_s16le", str(narration_master),
    )

    story_firsts: list[float] = []
    seen: set[str] = set()
    for item in timeline[1:]:
        story = str(item["story_id"])
        if story != "intro" and story not in seen:
            seen.add(story)
            story_firsts.append(float(item["start_sec"]))
    audio_args = ["ffmpeg", "-y", "-v", "error", "-i", str(narration_master)]
    audio_args += ["-f", "lavfi", "-i", f"sine=frequency=92:sample_rate=48000:duration={current:.3f}"]
    audio_args += ["-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate=48000:duration={current:.3f}"]
    cue_frequencies = tuple(audio_design.get("chapter_cue_frequencies") or (180, 920, 310, 560, 240))
    cue_duration = max(0.06, float(audio_design.get("chapter_cue_duration_seconds", 0.12)))
    cue_lead = max(0.0, float(audio_design.get("chapter_cue_lead_seconds", 0.10)))
    for index in range(len(story_firsts)):
        audio_args += ["-f", "lavfi", "-i", f"sine=frequency={cue_frequencies[index % len(cue_frequencies)]}:sample_rate=48000:duration={cue_duration:.3f}"]
    source_input_start = 3 + len(story_firsts)
    for window in source_windows:
        source_audio = safe_resolve_asset(root, window["audio"], label=f"source audio on {window['card_id']}")
        audio_args += [
            "-ss", f"{window['source_start_sec']:.3f}", "-t", f"{window['duration_sec']:.3f}",
            "-i", str(source_audio),
        ]
    filters = [
        "[0:a]volume=1.0[voice]",
        f"[1:a]lowpass=f=180,volume={float(audio_design.get('bed_tone_volume', 0.016)):.4f}[bedtone]",
        f"[2:a]lowpass=f=1100,highpass=f=80,volume={float(audio_design.get('bed_noise_volume', 0.005)):.4f}[bednoise]",
    ]
    mix_labels = ["[voice]", "[bedtone]", "[bednoise]"]
    for offset, start in enumerate(story_firsts, 3):
        label = f"cue{offset}"
        cue_start = max(0.0, start - cue_lead)
        cue_fade_start = max(0.01, cue_duration * 0.28)
        cue_fade_duration = max(0.02, cue_duration - cue_fade_start)
        filters.append(
            f"[{offset}:a]afade=t=out:st={cue_fade_start:.3f}:d={cue_fade_duration:.3f},"
            f"volume={float(audio_design.get('chapter_cue_volume', 0.085)):.4f},"
            f"adelay={round(cue_start * 1000)}:all=1[{label}]"
        )
        mix_labels.append(f"[{label}]")
    for offset, window in enumerate(source_windows, source_input_start):
        label = f"source{offset}"
        fade_in_sec = min(float(window["audio_fade_in_sec"]), float(window["duration_sec"]) / 2)
        fade_out_sec = min(float(window["audio_fade_out_sec"]), float(window["duration_sec"]) / 2)
        fade_out_at = max(0.0, float(window["duration_sec"]) - fade_out_sec)
        filters.append(
            f"[{offset}:a]aresample=48000,volume={float(window['audio_volume']):.3f},"
            f"afade=t=in:st=0:d={fade_in_sec:.3f},"
            f"afade=t=out:st={fade_out_at:.3f}:d={fade_out_sec:.3f},"
            f"adelay={round(float(window['start_sec']) * 1000)}:all=1[{label}]"
        )
        mix_labels.append(f"[{label}]")
    filters.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=longest:normalize=0,"
        f"atrim=duration={total_sec:.3f},loudnorm=I=-16:TP=-1.8:LRA=8[mix]"
    )
    master = final_dir / "master_narration_with_tail.wav"
    run(
        *audio_args, "-filter_complex", ";".join(filters), "-map", "[mix]",
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(master),
    )
    audio_sec = duration(master)
    protected_terms = tuple(str(term) for term in (data.get("video", {}).get("subtitle_keep_terms") or []))
    write_srt(cues, final_dir / "subtitles.srt", protected_terms=protected_terms)
    video_cfg = data.get("video") or {}
    write_single_box_ass(
        cues,
        final_dir / "subtitles_burn.ass",
        width=int(video_cfg.get("width", 1080)),
        height=int(video_cfg.get("height", 1920)),
        font_name=str(video_cfg.get("subtitle_font") or "Microsoft YaHei"),
        font_size=float(video_cfg.get("subtitle_font_size", 50)),
        margin_h=float(video_cfg.get("subtitle_margin_h", 82)),
        margin_v=float(video_cfg.get("subtitle_margin_v", 250)),
        wrapped_texts=[wrap_srt_caption(str(cue["text"]), protected_terms=protected_terms) for cue in cues],
    )
    (final_dir / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    timings_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": "PASS", "project": data.get("project"), "provider": "aliyun_qwen_audio",
        "model": config.model, "voice": config.voice, "instruction": config.instruction,
        "pre_roll_sec": pre_roll, "narration_and_original_audio_duration_sec": round(current - pre_roll, 3),
        "tail_hold_sec": tail, "master_audio_duration_sec": round(audio_sec, 3),
        "card_count": len(data["cards"]), "speech_unit_count": len(speech_cards),
        "subtitle_cue_count": len(cues), "original_sound_windows": source_windows,
        "tts_fingerprint": expected_tts_fingerprint,
        "audio_fingerprint": audio_fingerprint(root, data),
        "speech_boundary_gaps_sec": [row["pause_after_sec"] for row in rows[:-1]],
        "speech_tail_policy": "audible bounds with an 85ms consonant guard; 0.8s tail begins after the final guarded segment",
        "audio_design": {
            "bed_tone_volume": float(audio_design.get("bed_tone_volume", 0.016)),
            "bed_noise_volume": float(audio_design.get("bed_noise_volume", 0.005)),
            "chapter_cue_volume": float(audio_design.get("chapter_cue_volume", 0.085)),
            "chapter_cue_frequencies": cue_frequencies,
            "chapter_cue_duration_seconds": cue_duration,
            "chapter_cue_lead_seconds": cue_lead,
            "default_speech_gap_seconds": default_speech_gap,
        },
        "subtitle_content_validation": "PASS: each caption equals its tag-stripped spoken unit",
    }
    (final_dir / "master_sync_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return timeline, current - pre_roll, audio_sec


def reuse_audio(root: Path, data: dict[str, Any]) -> tuple[list[dict[str, Any]], float, float]:
    timeline_path = root / "final" / "timeline.json"
    audio_path = root / "final" / "master_narration_with_tail.wav"
    report_path = root / "final" / "master_sync_report.json"
    for path in (timeline_path, audio_path, report_path, root / "final" / "subtitles.srt"):
        if not path.is_file():
            raise FileNotFoundError(f"--reuse-audio requires {path}")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    if [item.get("id") for item in timeline] != [card.get("id") for card in data["cards"]]:
        raise ValueError("saved audio timeline card IDs do not match the current manifest")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected = audio_fingerprint(root, data)
    if report.get("audio_fingerprint") != expected:
        raise ValueError("saved audio does not match the current narration, voice, timing, source audio, or mix settings")
    write_subtitle_assets(root, data, timeline)
    return timeline, float(report["narration_and_original_audio_duration_sec"]), duration(audio_path)


def write_subtitle_assets(root: Path, data: dict[str, Any], timeline: list[dict[str, Any]]) -> None:
    """Write final/subtitles.srt and subtitles_burn.ass from verified spoken
    boundaries and the *current* video settings.

    Both build_audio and --reuse-audio go through here so subtitle layout
    (font, margins, protected terms) is never silently reused stale; the
    audio fingerprint deliberately excludes these text-only settings.
    """
    final_dir = root / "final"
    cards_by_id = {card.get("id"): card for card in data.get("cards", [])}
    cues: list[dict[str, Any]] = []
    for row in timeline:
        card = cards_by_id.get(row.get("id"))
        if card is None or not str(card.get("tts_text") or "").strip():
            continue
        cues.extend(build_caption_cues(card, float(row["start_sec"]), float(row["spoken_end_sec"])))
    if not cues:
        raise ValueError("timeline contains no spoken cards; refusing to write empty subtitle assets")
    video_cfg = data.get("video") or {}
    protected_terms = tuple(str(term) for term in (video_cfg.get("subtitle_keep_terms") or []))
    write_srt(cues, final_dir / "subtitles.srt", protected_terms=protected_terms)
    write_single_box_ass(
        cues,
        final_dir / "subtitles_burn.ass",
        width=int(video_cfg.get("width", 1080)),
        height=int(video_cfg.get("height", 1920)),
        font_name=str(video_cfg.get("subtitle_font") or "Microsoft YaHei"),
        font_size=float(video_cfg.get("subtitle_font_size", 50)),
        margin_h=float(video_cfg.get("subtitle_margin_h", 82)),
        margin_v=float(video_cfg.get("subtitle_margin_v", 250)),
        wrapped_texts=[wrap_srt_caption(str(cue["text"]), protected_terms=protected_terms) for cue in cues],
    )


def transition_plan(data: dict[str, Any], timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    video = data.get("video") or {}
    same_name = str(video.get("same_story_transition") or "fade")
    same_sec = float(video.get("same_story_transition_seconds", 0.24))
    cross_names = video.get("cross_story_transitions") or ["smoothleft", "smoothright"]
    if not isinstance(cross_names, list) or not cross_names:
        cross_names = ["smoothleft", "smoothright"]
    cross_sec = float(video.get("cross_story_transition_seconds", 0.36))
    plan: list[dict[str, Any]] = []
    chapter_index = 0
    for index in range(len(timeline) - 1):
        same_story = timeline[index]["story_id"] == timeline[index + 1]["story_id"]
        override = data["cards"][index].get("transition_to_next")
        if isinstance(override, dict):
            name = str(override.get("type") or override.get("transition") or same_name)
            seconds = float(override.get("duration_sec", same_sec if same_story else cross_sec))
        elif same_story:
            name, seconds = same_name, 0.14 if index == 0 else same_sec
        else:
            name, seconds = str(cross_names[chapter_index % len(cross_names)]), cross_sec
            chapter_index += 1
        if index == 0:
            # The cover segment is rendered with extra transition handles, so its
            # short nominal hold must not collapse the opening edit to ~3 frames.
            seconds = min(seconds, 0.45, float(timeline[index + 1]["duration_sec"]) * 0.30)
        else:
            seconds = min(
                seconds,
                float(timeline[index]["duration_sec"]) * 0.30,
                float(timeline[index + 1]["duration_sec"]) * 0.30,
            )
        seconds = max(1.0 / max(1, int(video.get("fps", 30))), seconds)
        plan.append({
            "from": timeline[index]["id"], "to": timeline[index + 1]["id"],
            "same_story": same_story, "transition": name, "duration_sec": round(seconds, 3),
            "boundary_sec": round(float(timeline[index]["end_sec"]), 3),
        })
    return plan


def file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    signature: dict[str, Any] = {"path": str(path.resolve()), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if stat.st_size <= 24 * 1024 * 1024:
        signature["sha256"] = sha256_file(path)
    return signature


def cache_key(kind: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"version": RENDERER_CACHE_VERSION, "kind": kind, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cached_render(
    cache_dir: Path,
    kind: str,
    payload: dict[str, Any],
    output: Path,
    build: Callable[[Path], None],
    stats: dict[str, Any],
) -> None:
    key = cache_key(kind, payload)
    cached = cache_dir / f"{key}.mp4"
    if cached.is_file() and cached.stat().st_size > 0:
        shutil.copyfile(cached, output)
        stats["hits"] += 1
        stats["items"].append({"kind": kind, "key": key, "status": "hit"})
        return
    started = time.perf_counter()
    staged = cache_dir / f".{key}.{os.getpid()}.tmp.mp4"
    staged.unlink(missing_ok=True)
    build(staged)
    if not staged.is_file() or staged.stat().st_size <= 0:
        raise RuntimeError(f"render stage did not produce output: {kind}")
    staged.replace(cached)
    shutil.copyfile(cached, output)
    elapsed = time.perf_counter() - started
    stats["misses"] += 1
    stats["items"].append({"kind": kind, "key": key, "status": "miss", "build_sec": round(elapsed, 3)})


def smoothstep(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def cover_image(path: Path, width: int, height: int) -> np.ndarray:
    image = cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
    source_height, source_width = image.shape[:2]
    scale = max(width / source_width, height / source_height)
    resized = cv2.resize(image, (max(1, round(source_width * scale)), max(1, round(source_height * scale))), interpolation=cv2.INTER_LANCZOS4)
    y = max(0, (resized.shape[0] - height) // 2)
    x = max(0, (resized.shape[1] - width) // 2)
    return cv2.cvtColor(resized[y:y + height, x:x + width], cv2.COLOR_BGR2RGB)


def layer_state(layer: dict[str, Any], seconds: float, total_sec: float, width: int, height: int) -> dict[str, float]:
    start = float(layer.get("start_sec", 0.0))
    reveal = max(0.08, float(layer.get("reveal_sec", 0.55)))
    progress = smoothstep((seconds - start) / reveal)
    timeline_progress = smoothstep(seconds / max(total_sec, 0.001))
    position = layer.get("position") or [0.5, 0.5]
    x, y = float(position[0]) * width, float(position[1]) * height
    scale = float(layer.get("from_scale", 1.0)) + (float(layer.get("to_scale", 1.0)) - float(layer.get("from_scale", 1.0))) * progress
    rotation = float(layer.get("from_rotation", 0.0)) + (float(layer.get("to_rotation", 0.0)) - float(layer.get("from_rotation", 0.0))) * progress
    opacity = float(layer.get("opacity", 1.0)) * progress
    blur = float(layer.get("from_blur", 0.0)) * (1.0 - progress)
    effect = str(layer.get("effect") or "focus_in")
    if effect == "rise_reveal":
        y += float(layer.get("rise_ratio", 0.07)) * height * (1.0 - progress)
    elif effect == "slide_left":
        x += float(layer.get("slide_ratio", 0.12)) * width * (1.0 - progress)
    elif effect == "stamp":
        overshoot = 1.0 + 0.12 * math.sin(progress * math.pi)
        scale *= overshoot
        rotation += -8.0 * (1.0 - progress)
    elif effect == "cursor_tap":
        origin = layer.get("from_position") or [max(0.0, float(position[0]) - 0.22), min(1.0, float(position[1]) + 0.16)]
        x = (float(origin[0]) + (float(position[0]) - float(origin[0])) * progress) * width
        y = (float(origin[1]) + (float(position[1]) - float(origin[1])) * progress) * height
        tap_at = float(layer.get("tap_at_sec", start + reveal + 0.15))
        tap_progress = min(1.0, max(0.0, (seconds - tap_at) / 0.28))
        scale *= 1.0 - 0.13 * math.sin(tap_progress * math.pi)
    drift = layer.get("drift") or [0.0, 0.0]
    x += float(drift[0]) * width * timeline_progress
    y += float(drift[1]) * height * timeline_progress
    shake = layer.get("shake") or {}
    shake_start = float(shake.get("start_sec", -1.0))
    shake_end = float(shake.get("end_sec", -1.0))
    if shake_start <= seconds <= shake_end and shake_end > shake_start:
        local = (seconds - shake_start) / (shake_end - shake_start)
        amplitude = float(shake.get("amplitude_px", 5.0)) * math.sin(math.pi * local)
        frequency = float(shake.get("frequency_hz", 13.0))
        x += amplitude * math.sin(seconds * frequency * math.tau)
        y += amplitude * 0.55 * math.sin(seconds * (frequency * 0.83) * math.tau + 0.7)
    pulse = layer.get("pulse") or {}
    pulse_start = float(pulse.get("start_sec", -1.0))
    pulse_duration = float(pulse.get("duration_sec", 0.0))
    if pulse_duration > 0 and pulse_start <= seconds <= pulse_start + pulse_duration:
        local = (seconds - pulse_start) / pulse_duration
        scale *= 1.0 + float(pulse.get("amount", 0.06)) * math.sin(math.pi * local)
    return {"x": x, "y": y, "scale": scale, "rotation": rotation, "opacity": opacity, "blur": blur}


def apply_animation_effects(frame: np.ndarray, effects: list[dict[str, Any]], seconds: float) -> None:
    height, width = frame.shape[:2]
    for effect in effects:
        kind = str(effect.get("type") or "")
        start = float(effect.get("start_sec", 0.0))
        effect_duration = max(0.001, float(effect.get("duration_sec", 1.0)))
        local = (seconds - start) / effect_duration
        if not 0.0 <= local <= 1.0:
            continue
        if kind == "scan_light":
            center = round((-0.15 + 1.3 * smoothstep(local)) * width)
            band = max(6, round(float(effect.get("width_ratio", 0.07)) * width))
            opacity = float(effect.get("opacity", 0.12)) * math.sin(math.pi * local)
            color = np.array(effect.get("color") or [210, 237, 255], dtype=np.float32)
            left, right = max(0, center - band), min(width, center + band)
            if left < right:
                distances = np.abs(np.arange(left, right) - center) / max(1, band)
                alpha = (1.0 - np.minimum(1.0, distances)) * opacity
                frame[:, left:right] = np.clip(
                    frame[:, left:right].astype(np.float32) * (1.0 - alpha[None, :, None])
                    + color[None, None, :] * alpha[None, :, None], 0, 255
                ).astype(np.uint8)
        elif kind == "vignette_pulse":
            strength = float(effect.get("strength", 0.12)) * math.sin(math.pi * local)
            yy, xx = np.ogrid[-1:1:complex(height), -1:1:complex(width)]
            mask = np.clip((xx * xx + yy * yy - 0.20) / 1.35, 0.0, 1.0)[:, :, None]
            frame[:] = np.clip(frame.astype(np.float32) * (1.0 - mask * strength), 0, 255).astype(np.uint8)


def render_layered_frame(
    base: np.ndarray,
    layers: list[tuple[dict[str, Any], np.ndarray]],
    animation: dict[str, Any],
    seconds: float,
    total_sec: float,
    *,
    width: int,
    height: int,
) -> np.ndarray:
    frame = base.copy()
    engine = str(animation.get("engine") or "legacy")
    ordered = sorted(
        enumerate(layers),
        key=lambda item: (float(item[1][0].get("z", 0.0)), item[0]),
    )
    for _, (layer, source) in ordered:
        if engine == "scene_v2":
            state = scene_v2_state(layer, seconds, total_sec, width, height)
        else:
            state = layer_state(layer, seconds, total_sec, width, height)
            state.update({
                "scale_x": state["scale"],
                "scale_y": state["scale"],
                "grounding": min(1.0, max(0.0, state["opacity"])),
                "phase": "legacy",
            })
        anchor_value = layer.get("anchor") or [0.5, 0.5]
        transformed = transform_layer(
            source,
            target_width=max(2, round(float(layer.get("width_ratio", 0.35)) * width)),
            scale_x=float(state["scale_x"]),
            scale_y=float(state["scale_y"]),
            rotation=float(state["rotation"]),
            blur=float(state["blur"]),
            anchor=(float(anchor_value[0]), float(anchor_value[1])),
        )
        shadow_config = layer.get("shadow")
        if engine == "scene_v2" and shadow_config is None:
            role = str(layer.get("role") or "subject")
            if role == "subject":
                shadow_config = {"type": "contact", "offset": [7, 14], "blur": 19, "opacity": 0.26}
            elif role == "evidence":
                shadow_config = {"type": "drop", "offset": [8, 12], "blur": 16, "opacity": 0.22}
        if isinstance(shadow_config, dict):
            shadow = shadow_layer(transformed, shadow_config, float(state["grounding"]))
            offset = shadow_config.get("offset") or [7, 12]
            resolution_scale = width / 1080.0
            composite(
                frame,
                shadow,
                float(state["x"]) + float(offset[0]) * resolution_scale,
                float(state["y"]) + float(offset[1]) * resolution_scale,
                float(state["opacity"]),
            )
        composite(frame, transformed, float(state["x"]), float(state["y"]), float(state["opacity"]))
    apply_animation_effects(frame, list(animation.get("effects") or []), seconds)
    return frame


def layered_video(
    background: Path,
    animation: dict[str, Any],
    seconds: float,
    output: Path,
    *,
    root: Path,
    profile: RenderProfile,
) -> None:
    frames = max(1, math.ceil(seconds * profile.fps))
    base = cover_image(background, profile.width, profile.height)
    layers = [
        (
            dict(item),
            load_premultiplied(safe_resolve_asset(root, item["asset"], label="animation layer")),
        )
        for item in animation.get("layers") or []
    ]
    command = [
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{profile.width}x{profile.height}", "-r", str(profile.fps), "-i", "-", "-an",
        "-c:v", "libx264", "-preset", profile.intermediate_preset, "-crf", str(profile.intermediate_crf),
        "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        str(output),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        if process.stdin is None:
            raise RuntimeError("unable to open ffmpeg frame pipe")
        for frame_index in range(frames):
            current = min(seconds, frame_index / profile.fps)
            frame = render_layered_frame(
                base,
                layers,
                animation,
                current,
                seconds,
                width=profile.width,
                height=profile.height,
            )
            process.stdin.write(frame.tobytes())
        process.stdin.close()
        return_code = process.wait()
        if return_code:
            raise subprocess.CalledProcessError(return_code, command)
    except BaseException:
        if process.stdin and not process.stdin.closed:
            process.stdin.close()
        process.kill()
        process.wait()
        raise


def static_video(image: Path, seconds: float, output: Path, *, profile: RenderProfile) -> None:
    frames = max(1, math.ceil(seconds * profile.fps))
    run(
        "ffmpeg", "-y", "-v", "error", "-loop", "1", "-framerate", str(profile.fps), "-i", str(image),
        "-vf", f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=increase,crop={profile.width}:{profile.height},setsar=1,format=yuv420p",
        "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", profile.intermediate_preset, "-crf", str(profile.intermediate_crf),
        "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", str(output),
    )


def resolve_foreground_height(
    presentation: dict[str, Any],
    *,
    output_height: int,
    reference_height: int,
    default_logical_height: int,
    minimum: int,
) -> int:
    """Resolve a source-window height consistently across render profiles."""
    if "foreground_height_ratio" in presentation:
        ratio = min(1.0, max(0.0, float(presentation["foreground_height_ratio"])))
        resolved = round(output_height * ratio)
    else:
        logical_height = float(presentation.get("foreground_height", default_logical_height))
        resolved = round(logical_height * output_height / max(1, reference_height))
    return max(minimum, min(output_height, resolved))


def source_video(
    source: Path,
    start: float,
    seconds: float,
    output: Path,
    *,
    profile: RenderProfile,
    presentation: dict[str, Any],
    backdrop: Path | None = None,
    reference_height: int | None = None,
) -> None:
    width, height, fps = profile.width, profile.height, profile.fps
    design_height = max(1, int(reference_height or height))
    frames = max(1, math.ceil(seconds * fps))
    mode = str(presentation.get("mode") or "center_window")
    blur = max(0.0, float(presentation.get("background_blur", 34)))
    vertical_position = min(1.0, max(0.0, float(presentation.get("vertical_position", 0.5))))
    overlay_y = f"(H-h)*{vertical_position:.4f}"
    if mode == "cover_crop":
        focal_x = min(1.0, max(0.0, float(presentation.get("focal_x", 0.5))))
        focal_y = min(1.0, max(0.0, float(presentation.get("focal_y", 0.5))))
        graph = (
            f"[0:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:(iw-ow)*{focal_x:.4f}:(ih-oh)*{focal_y:.4f},setsar=1,format=yuv420p[out]"
        )
        input_args = ["-ss", f"{start:.3f}", "-i", str(source)]
    elif mode in {"center_window", "contain"}:
        foreground_height = resolve_foreground_height(
            presentation,
            output_height=height,
            reference_height=design_height,
            default_logical_height=design_height if mode == "contain" else min(620, design_height),
            minimum=64,
        )
        graph = (
            f"[0:v]fps={fps},split=2[back][front];"
            f"[back]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},gblur=sigma={blur:.2f}[bg];"
            f"[front]scale={width}:{foreground_height}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:{overlay_y},setsar=1,format=yuv420p[out]"
        )
        input_args = ["-ss", f"{start:.3f}", "-i", str(source)]
    elif mode == "scene_window":
        if backdrop is None:
            raise ValueError("scene_window presentation requires a settled scene backdrop")
        foreground_width = max(96, min(width, int(width * float(presentation.get("foreground_width_ratio", 0.88)))))
        foreground_height = resolve_foreground_height(
            presentation,
            output_height=height,
            reference_height=design_height,
            default_logical_height=min(720, design_height),
            minimum=96,
        )
        x = round((width - foreground_width) * min(1.0, max(0.0, float(presentation.get("horizontal_position", 0.5)))))
        y = round((height - foreground_height) * vertical_position)
        fade_in = min(seconds / 2, max(0.0, float(presentation.get("video_fade_in_sec", 0.16))))
        fade_out = min(seconds / 2, max(0.0, float(presentation.get("video_fade_out_sec", 0.18))))
        fade_filters = []
        if fade_in > 0:
            fade_filters.append(f"fade=t=in:st=0:d={fade_in:.3f}:alpha=1")
        if fade_out > 0:
            fade_filters.append(f"fade=t=out:st={max(0.0, seconds - fade_out):.3f}:d={fade_out:.3f}:alpha=1")
        fade_chain = "," + ",".join(fade_filters) if fade_filters else ""
        border = str(presentation.get("border_color") or "0xE8E0D0@0.72")
        shadow_x = x + max(2, round(width * 0.008))
        shadow_y = y + max(3, round(height * 0.007))
        graph = (
            f"[1:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1[scene];"
            f"[scene]drawbox=x={shadow_x}:y={shadow_y}:w={foreground_width}:h={foreground_height}:color=black@0.24:t=fill[shadowed];"
            f"[0:v]fps={fps},scale={foreground_width}:{foreground_height}:force_original_aspect_ratio=decrease,"
            f"format=rgba,pad={foreground_width}:{foreground_height}:(ow-iw)/2:(oh-ih)/2:color=black@0.0{fade_chain}[fg];"
            f"[shadowed][fg]overlay={x}:{y}:format=auto,"
            f"drawbox=x={x}:y={y}:w={foreground_width}:h={foreground_height}:color={border}:t=3,"
            f"setsar=1,format=yuv420p[out]"
        )
        input_args = [
            "-ss", f"{start:.3f}", "-i", str(source),
            "-loop", "1", "-framerate", str(fps), "-i", str(backdrop),
        ]
    else:
        raise ValueError(f"unsupported original_clip presentation mode: {mode}")
    run(
        "ffmpeg", "-y", "-v", "error", *input_args,
        "-filter_complex", graph, "-map", "[out]", "-frames:v", str(frames), "-an",
        "-c:v", "libx264", "-preset", profile.intermediate_preset, "-crf", str(profile.intermediate_crf), "-pix_fmt", "yuv420p",
        "-r", str(fps), "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", str(output),
    )


def concat_video_parts(parts: list[Path], output: Path, profile: RenderProfile) -> None:
    if len(parts) == 1:
        shutil.copyfile(parts[0], output)
        return
    inputs: list[str] = []
    for part in parts:
        inputs.extend(["-i", str(part)])
    normalized = []
    for index in range(len(parts)):
        normalized.append(f"[{index}:v]setsar=1[v{index}]")
    labels = "".join(f"[v{index}]" for index in range(len(parts)))
    run(
        "ffmpeg", "-y", "-v", "error", *inputs,
        "-filter_complex", f"{';'.join(normalized)};{labels}concat=n={len(parts)}:v=1:a=0[out]", "-map", "[out]", "-an",
        "-c:v", "libx264", "-preset", profile.intermediate_preset, "-crf", str(profile.intermediate_crf), "-pix_fmt", "yuv420p",
        "-r", str(profile.fps), str(output),
    )


def validate_transition_frames(video: Path, boundaries: list[float], work: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    sample_dir = work / "boundary_samples"
    sample_dir.mkdir(exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open video for transition checks: {video}")
    try:
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        for index, boundary in enumerate(boundaries):
            targets = [(delta, max(0.0, boundary + delta)) for delta in (-0.05, 0.0, 0.05)]
            frame_targets = {max(0, round(timestamp * fps)): (delta, timestamp) for delta, timestamp in targets}
            start_frame, end_frame = min(frame_targets), max(frame_targets)
            capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            decoded: dict[int, np.ndarray] = {}
            for frame_number in range(start_frame, end_frame + 1):
                ok, image = capture.read()
                if not ok or image is None:
                    raise RuntimeError(f"cannot decode transition frame near {boundary:.3f}s")
                if frame_number in frame_targets:
                    decoded[frame_number] = image
            for frame_number in sorted(frame_targets):
                delta, timestamp = frame_targets[frame_number]
                image = decoded[frame_number]
                frame = sample_dir / f"b{index + 1:02d}_{delta:+.2f}.png"
                cv2.imwrite(str(frame), image)
                rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                stat = ImageStat.Stat(Image.fromarray(rgb))
                mean_luma = sum(stat.mean) / 3
                extrema_span = max(high for _, high in stat.extrema) - min(low for low, _ in stat.extrema)
                passed = 3.0 < mean_luma < 252.0 and extrema_span > 12
                results.append({
                    "boundary_index": index + 1, "timestamp_sec": round(timestamp, 3),
                    "mean_luma": round(mean_luma, 2), "extrema_span": extrema_span,
                    "status": "PASS" if passed else "FAIL",
                })
                if not passed:
                    raise RuntimeError(f"blank transition frame near {timestamp:.3f}s")
    finally:
        capture.release()
    return results


def output_filename(data: dict[str, Any]) -> str:
    configured = str((data.get("video") or {}).get("output_filename") or "").strip()
    if configured:
        if Path(configured).name != configured or not configured.lower().endswith(".mp4"):
            raise ValueError("video.output_filename must be a plain .mp4 filename")
        return configured
    suffix = "EN" if str(data.get("language") or "").lower().startswith("en") else "ZH"
    story_count = len({
        str(card.get("story_id") or card.get("id"))
        for card in data.get("cards", [])
        if str(card.get("story_id") or "") != "intro"
    })
    return f"global-hotspot-radar-top{story_count}_{suffix}.mp4"


def render_video(
    root: Path,
    data: dict[str, Any],
    timeline: list[dict[str, Any]],
    audio_sec: float,
    *,
    mode: str,
) -> tuple[Path, float, dict[str, Any]]:
    build_started = time.perf_counter()
    final_dir = root / "final"
    preview_dir = root / "preview"
    final_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    work = root / "temp" / f"render_{mode}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    cache_dir = root / "cache" / "render"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cfg = data.get("video") or {}
    profile = render_profile(data, mode)
    transitions = transition_plan(data, timeline)
    motion_report = analyze_motion_plan(data, timeline)
    motion_report_dir = final_dir if mode == "release" else preview_dir
    motion_report_name = "motion_quality_report.json" if mode == "release" else f"motion_quality_report_{mode}.json"
    (motion_report_dir / motion_report_name).write_text(
        json.dumps(motion_report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if mode == "release" and motion_report["blocking"]:
        raise ValueError("release motion quality checks failed: " + "; ".join(motion_report["errors"]))
    cache_stats: dict[str, Any] = {"hits": 0, "misses": 0, "items": []}
    stage_times: dict[str, float] = {}
    segments: list[Path] = []
    segment_report: list[dict[str, Any]] = []
    animation_windows: list[dict[str, Any]] = []

    def profile_payload() -> dict[str, Any]:
        return {
            "mode": "preview" if profile.width < int(cfg.get("width", 1080)) else "candidate",
            "width": profile.width,
            "height": profile.height,
            "fps": profile.fps,
            "preset": profile.intermediate_preset,
            "crf": profile.intermediate_crf,
        }

    def render_card_part(card: dict[str, Any], image: Path, seconds: float, output: Path, part_name: str) -> None:
        animation = card.get("animation")
        payload: dict[str, Any] = {
            "part": part_name,
            "seconds": round(seconds, 6),
            "background": file_signature(image),
            "profile": profile_payload(),
        }
        if animation:
            payload["animation"] = animation
            payload["layers"] = [
                file_signature(safe_resolve_asset(root, layer["asset"], label=f"animation layer on {card['id']}"))
                for layer in animation.get("layers") or []
            ]
            cached_render(
                cache_dir,
                "layered",
                payload,
                output,
                lambda staged: layered_video(image, animation, seconds, staged, root=root, profile=profile),
                cache_stats,
            )
        else:
            cached_render(
                cache_dir,
                "static",
                payload,
                output,
                lambda staged: static_video(image, seconds, staged, profile=profile),
                cache_stats,
            )

    def settled_scene(card: dict[str, Any], image: Path, at_sec: float, total_sec: float, output: Path) -> Path:
        animation = card.get("animation")
        if not animation:
            return image
        base = cover_image(image, profile.width, profile.height)
        layers = [
            (
                dict(layer),
                load_premultiplied(
                    safe_resolve_asset(root, layer["asset"], label=f"animation layer on {card['id']}")
                ),
            )
            for layer in animation.get("layers") or []
        ]
        frame = render_layered_frame(
            base,
            layers,
            animation,
            max(0.0, at_sec),
            max(0.001, total_sec),
            width=profile.width,
            height=profile.height,
        )
        Image.fromarray(frame).save(output)
        return output

    segment_started = time.perf_counter()
    for index, (card, timing) in enumerate(zip(data["cards"], timeline)):
        nominal = float(timing["duration_sec"])
        overlap = float(transitions[index]["duration_sec"]) if index < len(transitions) else 0.0
        render_sec = nominal + overlap
        image = safe_resolve_asset(root, card["image"], label=f"card image on {card['id']}")
        output = work / f"segment_{index + 1:02d}.mp4"
        clip = card.get("original_clip")
        clip_report = None
        if not clip:
            render_card_part(card, image, render_sec, output, f"segment-{index + 1}")
            if card.get("animation"):
                animation_windows.append({
                    "card_id": card["id"],
                    "start_sec": float(timing["start_sec"]),
                    "end_sec": float(timing["end_sec"]),
                    "effects": sorted({str(layer.get("effect") or "focus_in") for layer in card["animation"].get("layers") or []}),
                })
        else:
            clip_sec = float(clip["duration_sec"])
            spoken_sec = float(timing["spoken_end_sec"]) - float(timing["start_sec"])
            post_sec = max(0.0, nominal - spoken_sec - clip_sec) + overlap
            parts: list[Path] = []
            if spoken_sec > 0.001:
                still_before = work / f"still_before_{index + 1:02d}.mp4"
                render_card_part(card, image, spoken_sec, still_before, f"pre-source-{index + 1}")
                parts.append(still_before)
                if card.get("animation"):
                    animation_windows.append({
                        "card_id": card["id"],
                        "start_sec": float(timing["start_sec"]),
                        "end_sec": float(timing["spoken_end_sec"]),
                        "effects": sorted({str(layer.get("effect") or "focus_in") for layer in card["animation"].get("layers") or []}),
                    })
            settled_path = settled_scene(
                card,
                image,
                spoken_sec,
                nominal,
                work / f"settled_scene_{index + 1:02d}.png",
            )
            moving = work / f"source_{index + 1:02d}.mp4"
            presentation = dict(clip.get("presentation") or {})
            if "foreground_height" in clip and "foreground_height" not in presentation:
                presentation["foreground_height"] = clip["foreground_height"]
            source_path = safe_resolve_asset(root, clip["video"], label=f"original clip video on {card['id']}")
            source_payload = {
                "source": file_signature(source_path),
                "start": float(clip["source_start_sec"]),
                "seconds": clip_sec,
                "presentation": presentation,
                "profile": profile_payload(),
                "source_window_geometry_contract": 2,
            }
            if str(presentation.get("mode") or "center_window") == "scene_window":
                source_payload["backdrop"] = file_signature(settled_path)
            cached_render(
                cache_dir,
                "source",
                source_payload,
                moving,
                lambda staged: source_video(
                    source_path,
                    float(clip["source_start_sec"]),
                    clip_sec,
                    staged,
                    profile=profile,
                    presentation=presentation,
                    backdrop=settled_path,
                    reference_height=int(data["video"].get("height") or profile.height),
                ),
                cache_stats,
            )
            parts.append(moving)
            if post_sec > 0.001:
                still_after = work / f"still_after_{index + 1:02d}.mp4"
                static_payload = {
                    "part": f"post-source-{index + 1}",
                    "seconds": round(post_sec, 6),
                    "background": file_signature(settled_path),
                    "profile": profile_payload(),
                }
                cached_render(
                    cache_dir,
                    "static",
                    static_payload,
                    still_after,
                    lambda staged: static_video(settled_path, post_sec, staged, profile=profile),
                    cache_stats,
                )
                parts.append(still_after)
            concat_payload = {
                "parts": [file_signature(part) for part in parts],
                "profile": profile_payload(),
            }
            cached_render(
                cache_dir,
                "concat",
                concat_payload,
                output,
                lambda staged: concat_video_parts(parts, staged, profile),
                cache_stats,
            )
            clip_report = {
                "video": str(source_path),
                "audio": str(safe_resolve_asset(root, clip["audio"], label=f"original clip audio on {card['id']}")),
                "source_start_sec": float(clip["source_start_sec"]), "duration_sec": clip_sec,
                "label": clip["label"], "presentation": presentation,
                "audio_volume": float(clip.get("audio_volume", 0.78)),
                "audio_fade_in_sec": float(clip.get("audio_fade_in_sec", 0.08)),
                "audio_fade_out_sec": float(clip.get("audio_fade_out_sec", 0.14)),
            }
        segments.append(output)
        motion = "layered_semantic_animation" if card.get("animation") else "static_generated_card"
        if clip:
            motion += "_then_source_clip_then_settled_scene"
        segment_report.append({
            "id": card["id"], "story_id": card.get("story_id", card["id"]), "image": str(image),
            "start_sec": timing["start_sec"], "end_sec": timing["end_sec"],
            "nominal_duration_sec": round(nominal, 3), "render_duration_sec": round(render_sec, 3),
            "motion": motion, "original_clip": clip_report,
        })
    stage_times["segments_sec"] = round(time.perf_counter() - segment_started, 3)

    inputs: list[str] = []
    for segment in segments:
        inputs.extend(["-i", str(segment)])
    filters: list[str] = []
    cumulative = float(timeline[0]["duration_sec"])
    prior = "[0:v]"
    for index, item in enumerate(transitions):
        label = f"[v{index + 1}]"
        filters.append(
            f"{prior}[{index + 1}:v]xfade=transition={item['transition']}:duration={item['duration_sec']}:offset={cumulative:.3f}{label}"
        )
        prior = label
        cumulative += float(timeline[index + 1]["duration_sec"])
    burn_subtitles = bool(cfg.get("burn_subtitles", False))
    if burn_subtitles:
        subtitle_path = (final_dir / "subtitles_burn.ass").as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        subtitle_label = "[vsub]"
        filters.append(
            f"{prior}subtitles=filename='{subtitle_path}'{subtitle_label}"
        )
        prior = subtitle_label
    filter_file = work / "xfade.txt"
    filter_file.write_text(";\n".join(filters) + "\n", encoding="utf-8")
    silent = work / "silent_with_transitions.mp4"
    final_encode_started = time.perf_counter()
    run(
        "ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex_script", str(filter_file),
        "-map", prior, "-an", "-c:v", "libx264", "-preset", profile.final_preset, "-crf", str(profile.final_crf),
        "-pix_fmt", "yuv420p", "-r", str(profile.fps), "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", str(silent),
    )
    stage_times["final_video_encode_sec"] = round(time.perf_counter() - final_encode_started, 3)
    base_name = output_filename(data)
    if mode == "release":
        video = final_dir / base_name
    else:
        video = preview_dir / f"{mode}_{base_name}"
    mux_started = time.perf_counter()
    run(
        "ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(final_dir / "master_narration_with_tail.wav"),
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(video),
    )
    stage_times["audio_mux_sec"] = round(time.perf_counter() - mux_started, 3)
    video_sec = duration(video)
    if video_sec + 0.050 < audio_sec:
        raise RuntimeError(f"video ends before audio: {video_sec:.3f} < {audio_sec:.3f}")

    cover_png: Path | None = None
    if mode == "release":
        cover_png = final_dir / "cover_3x4.png"
        cover_width, cover_height = int(cfg.get("cover_width", profile.width)), int(cfg.get("cover_height", 1440))
        cover_source = safe_resolve_asset(
            root,
            cfg.get("publish_cover_asset") or data["cards"][0]["image"],
            label="publish cover asset",
        )
        cover_focal_y = min(1.0, max(0.0, float(cfg.get("cover_focal_y", 0.5))))
        run(
            "ffmpeg", "-y", "-v", "error", "-i", str(cover_source),
            "-vf", (
                f"scale={cover_width}:{profile.height}:force_original_aspect_ratio=increase,"
                f"crop={cover_width}:{cover_height}:0:(ih-oh)*{cover_focal_y:.4f}"
            ),
            "-frames:v", "1", str(cover_png),
        )
        run("ffmpeg", "-y", "-v", "error", "-i", str(cover_png), "-q:v", "2", "-frames:v", "1", str(final_dir / "cover_3x4.jpg"))

    diagnostics_started = time.perf_counter()
    sheet_frames = profile.contact_sheet_frames
    columns = 4 if sheet_frames > 9 else 3
    rows = math.ceil(sheet_frames / columns)
    sample_fps = sheet_frames / max(video_sec, 1.0)
    contact_sheet = preview_dir / ("contact_sheet.jpg" if mode == "release" else f"contact_sheet_{mode}.jpg")
    run(
        "ffmpeg", "-y", "-v", "error", "-i", str(video),
        "-vf", f"fps={sample_fps:.8f},scale=270:480:force_original_aspect_ratio=decrease,pad=270:480:(ow-iw)/2:(oh-ih)/2:color=0x09192a,tile={columns}x{rows}:nb_frames={sheet_frames}",
        "-frames:v", "1", "-q:v", "2", str(contact_sheet),
    )
    frame_checks = (
        validate_transition_frames(video, [float(item["boundary_sec"]) for item in transitions], work)
        if profile.boundary_checks else []
    )
    stage_times["technical_diagnostics_sec"] = round(time.perf_counter() - diagnostics_started, 3)
    total_build_sec = round(time.perf_counter() - build_started, 3)
    performance = {
        "mode": mode,
        "profile": {
            "width": profile.width, "height": profile.height, "fps": profile.fps,
            "final_preset": profile.final_preset, "final_crf": profile.final_crf,
            "intermediate_preset": profile.intermediate_preset, "intermediate_crf": profile.intermediate_crf,
        },
        "stage_times": stage_times,
        "cache": cache_stats,
        "total_build_sec": total_build_sec,
        "real_time_factor": round(total_build_sec / max(video_sec, 0.001), 3),
    }
    report = {
        "status": "PASS", "renderer": str(Path(__file__).resolve()), "render_mode": mode,
        "video": str(video), "cover": None if cover_png is None else str(cover_png),
        "subtitle_sidecar": str(final_dir / "subtitles.srt"), "subtitles_burned": burn_subtitles,
        "card_count": len(data["cards"]),
        "distinct_generated_images": len({card["image"] for card in data["cards"]}),
        "video_duration_sec": round(video_sec, 3), "audio_duration_sec": round(audio_sec, 3),
        "pre_roll_sec": float(cfg.get("pre_roll_seconds", 0.35)), "tail_hold_sec": float(cfg["tail_hold_seconds"]),
        "segments": segment_report, "animation_windows": animation_windows,
        "motion_quality_report": str(motion_report_dir / motion_report_name),
        "motion_quality": motion_report,
        "transitions": transitions, "transition_frame_checks": frame_checks,
        "visual_text_policy": (
            "Each content block and cover is a complete generated bitmap. Code only composites transparent generated layers; "
            "when enabled, accessibility subtitles are burned from the verified SRT while the same SRT remains available as a sidecar."
        ),
        "motion": (
            "Motion is manifest-driven. scene_v2 uses phased anticipation/action/settle/hold timing, premultiplied-alpha "
            "compositing, z order, anchors, and contextual shadows; any legacy continuous drift is disclosed by the "
            "motion quality report instead of being silently described as stable."
        ),
        "audio_design": "Aliyun custom narration, restrained synthetic bed and cues, plus optional source-sound windows placed after complete spoken units.",
        "performance": performance,
        "ffprobe": probe(video),
        "sha256": sha256_file(video) if mode == "release" else None,
    }
    report_dir = final_dir if mode == "release" else preview_dir
    report_name = "technical_validation.json" if mode == "release" else f"technical_validation_{mode}.json"
    performance_name = "build_performance_report.json" if mode == "release" else f"build_performance_{mode}.json"
    (report_dir / report_name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / performance_name).write_text(json.dumps(performance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return video, video_sec, performance


def update_episode_manifest(root: Path, data: dict[str, Any], video: Path, video_sec: float) -> None:
    path = root / "episode_manifest.json"
    episode = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    cfg = data.get("video") or {}
    episode.update({
        "series": "全球热点雷达", "language": data.get("language"),
        "canvas": {"width": int(cfg.get("width", 1080)), "height": int(cfg.get("height", 1920)), "fps": int(cfg.get("fps", 30))},
        "cover": {"width": int(cfg.get("cover_width", 1080)), "height": int(cfg.get("cover_height", 1440))},
        "duration_sec": round(video_sec, 3), "cover_hold_sec": float(cfg.get("pre_roll_seconds", 0.35)),
        "tail_hold_sec": float(cfg["tail_hold_seconds"]), "card_count": len(data["cards"]),
        "distinct_generated_images": len({card["image"] for card in data["cards"]}),
        "original_sound_windows": sum(1 for card in data["cards"] if card.get("original_clip")),
        "final_video": str(video.relative_to(root)), "publish_cover": "final/cover_3x4.jpg", "publish_info": "publish_info.md",
    })
    path.write_text(json.dumps(episode, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True, help="Path to content_manifest.json")
    parser.add_argument(
        "--mode",
        choices=("preview", "candidate", "release"),
        default="release",
        help="preview=540p fast proof, candidate=1080p fast review, release=1080p final quality",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--reuse-tts", action="store_true", help="Reuse combined provider TTS and rebuild timing/audio/video")
    group.add_argument("--reuse-audio", action="store_true", help="Reuse final master audio/timeline and render visuals only")
    args = parser.parse_args()
    manifest = args.manifest.resolve()
    root = manifest.parent
    data = json.loads(manifest.read_text(encoding="utf-8"))
    validate_manifest(root, data)
    if args.reuse_audio:
        timeline, narration_sec, audio_sec = reuse_audio(root, data)
    else:
        timeline, narration_sec, audio_sec = build_audio(root, data, reuse_tts=args.reuse_tts)
    video, video_sec, performance = render_video(root, data, timeline, audio_sec, mode=args.mode)
    if args.mode == "release":
        update_episode_manifest(root, data, video, video_sec)
    print(json.dumps({
        "video": str(video), "video_duration_sec": round(video_sec, 3),
        "narration_and_source_duration_sec": round(narration_sec, 3),
        "audio_duration_sec": round(audio_sec, 3), "cards": len(timeline),
        "mode": args.mode, "performance": performance,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
