from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import lightweight_asset_signature, safe_resolve_asset, stable_json_hash


def default_voice_instruction(language: str) -> str:
    if language.lower().startswith("zh"):
        return (
            "像经验丰富、做过事实核查的女性新闻编辑一样播报：可信、具体、通俗自然，不使用广告腔或AI总结腔。"
            "整体采用舒展的自然中速；关键事实、数字、来源限定和转折处主动停顿，句间保留完整呼吸。"
            "按内容克制地切换好奇、兴奋、严肃和共情，执行所有情绪与拟声标签但绝不读出标签。"
        )
    return (
        "Sound like an experienced female news editor who has checked the primary material: credible, specific, "
        "conversational, and never promotional or AI-summary-like. Use an unhurried natural pace, pause on evidence, "
        "numbers, attribution, and turns, preserve full breaths between units, vary emotion with restraint, and perform "
        "every supported emotion or sound tag without reading the tag aloud."
    )


def voice_identity(data: dict[str, Any]) -> dict[str, str]:
    voice = data.get("voice") or {}
    language = str(data.get("language") or "zh-CN")
    return {
        "provider": str(voice.get("provider") or "alibaba_qwen_tts"),
        "model": str(voice.get("model") or "qwen-audio-3.0-tts-plus"),
        "voice": str(voice.get("voice") or "longanlingxin"),
        "instruction": str(voice.get("instruction") or default_voice_instruction(language)),
    }


def tts_fingerprint(data: dict[str, Any]) -> str:
    speech_cards = data.get("cards", [])[1:]
    return stable_json_hash({
        "contract": "global-hotspot-tts-v2",
        "voice": voice_identity(data),
        "units": [
            {
                "id": card.get("id"),
                "tts_text": card.get("tts_text"),
                "caption_text": card.get("caption_text"),
            }
            for card in speech_cards
        ],
    })


def audio_fingerprint(root: Path, data: dict[str, Any]) -> str:
    cards: list[dict[str, Any]] = []
    for card in data.get("cards", [])[1:]:
        clip = card.get("original_clip")
        clip_payload = None
        if isinstance(clip, dict):
            audio_path = safe_resolve_asset(root, clip.get("audio"), label=f"original_clip audio on {card.get('id')}")
            clip_payload = {
                "audio": lightweight_asset_signature(audio_path),
                "source_start_sec": clip.get("source_start_sec"),
                "duration_sec": clip.get("duration_sec"),
                "audio_volume": clip.get("audio_volume", 0.78),
                "audio_fade_in_sec": clip.get("audio_fade_in_sec", 0.08),
                "audio_fade_out_sec": clip.get("audio_fade_out_sec", 0.14),
            }
        cards.append({
            "id": card.get("id"),
            "tts_text": card.get("tts_text"),
            "caption_text": card.get("caption_text"),
            "caption_chunks": card.get("caption_chunks"),
            "pause_after_seconds": card.get("pause_after_seconds"),
            "original_clip": clip_payload,
        })
    video = data.get("video") or {}
    return stable_json_hash({
        "contract": "global-hotspot-master-audio-v2",
        "tts_fingerprint": tts_fingerprint(data),
        "cards": cards,
        "pre_roll_seconds": video.get("pre_roll_seconds", 0.35),
        "tail_hold_seconds": video.get("tail_hold_seconds"),
        "audio_design": data.get("audio") or {},
    })
