"""Dependency-free checks for the existing caption and SRT contracts.

This checks text/timing records, not spoken pronunciation or burned-in pixels.
Whitespace can change during wrapping; punctuation, case and digits cannot.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata
from typing import Any

TAGS = frozenset({
    "curious", "excited", "serious", "sarcastic", "mischievously", "empathetic",
    "giggles", "laughing", "sighing", "clears throat", "whispers", "very fast", "very slowly",
})
TAG_RE = re.compile(r"\[([^\]]*)\]")
STAMP = r"([0-9]{2,}):([0-5][0-9]):([0-5][0-9]),([0-9]{3})"
TIMING_RE = re.compile(rf"^{STAMP}\s+-->\s+{STAMP}$")


def compact(text: str) -> str:
    return "".join(unicodedata.normalize("NFC", text).split())


def strip_voice_tags(text: str) -> str:
    for tag in TAG_RE.findall(text):
        if tag not in TAGS:
            raise ValueError(f"unsupported TTS tag: {tag!r}")
    # Match the renderer's tag handling without importing its TTS provider.
    return re.sub(r"\s+", " ", TAG_RE.sub("", text)).strip()


def split_terms(parts: list[str], terms: list[str]) -> list[str]:
    """Find explicitly protected terms crossing page/line boundaries."""
    joined = "".join(compact(part) for part in parts)
    boundaries: list[int] = []
    position = 0
    for part in parts[:-1]:
        position += len(compact(part))
        boundaries.append(position)
    broken: list[str] = []
    for term in terms:
        needle = compact(term)
        if not needle:
            continue
        start = joined.find(needle)
        while start >= 0:
            if any(start < boundary < start + len(needle) for boundary in boundaries):
                broken.append(term)
                break
            start = joined.find(needle, start + 1)
    return broken


def expected_pages(content: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Validate narration/caption/page consistency and return ordered pages."""
    errors: list[str] = []
    pages: list[str] = []
    cards = content.get("cards")
    if not isinstance(cards, list) or not cards:
        return [], ["content_manifest.cards must be a non-empty list"]
    video = content.get("video") or {}
    if not isinstance(video, dict):
        return [], ["content_manifest.video must be an object"]
    terms = video.get("subtitle_keep_terms", [])
    if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
        return [], ["video.subtitle_keep_terms must be a list of non-empty strings"]
    for i, card in enumerate(cards):
        label = f"cards[{i}]"
        if not isinstance(card, dict):
            errors.append(f"{label} must be an object")
            continue
        speech = card.get("tts_text", "")
        if speech is None:
            speech = ""
        if not isinstance(speech, str):
            errors.append(f"{label}.tts_text must be a string")
            continue
        if not speech.strip():
            continue
        caption = card.get("caption_text")
        if not isinstance(caption, str) or not caption.strip():
            errors.append(f"{label}.caption_text must be a non-empty string")
            continue
        try:
            if compact(strip_voice_tags(speech)) != compact(caption):
                errors.append(f"{label}: caption_text differs from untagged narration")
        except ValueError as exc:
            errors.append(f"{label}: {exc}")
        chunks = card.get("caption_chunks")
        chunks = [caption] if chunks is None else chunks
        if not isinstance(chunks, list) or not chunks or any(not isinstance(chunk, str) or not chunk.strip() for chunk in chunks):
            errors.append(f"{label}.caption_chunks must be a non-empty string list")
            continue
        if compact("".join(chunks)) != compact(caption):
            errors.append(f"{label}: caption_chunks change caption text (including punctuation/digits)")
        broken = split_terms(chunks, terms)
        if broken:
            errors.append(f"{label}: protected terms split across caption pages: {broken}")
        pages.extend(chunks)
    return pages, errors


@dataclass(frozen=True)
class Cue:
    number: int
    start_ms: int
    end_ms: int
    lines: tuple[str, ...]


def _milliseconds(values: tuple[str, ...]) -> int:
    hours, minutes, seconds, millis = map(int, values)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(text: str) -> list[Cue]:
    source = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        raise ValueError("SRT is empty")
    cues: list[Cue] = []
    for block_index, block in enumerate(re.split(r"\n[ \t]*\n+", source), 1):
        lines = block.splitlines()
        if len(lines) < 3 or not re.fullmatch(r"[0-9]+", lines[0].strip()):
            raise ValueError(f"SRT block {block_index}: expected index, timestamp and text")
        match = TIMING_RE.fullmatch(lines[1].strip())
        if not match or any(not line.strip() for line in lines[2:]):
            raise ValueError(f"SRT block {block_index}: malformed timestamp or empty text")
        cues.append(Cue(int(lines[0]), _milliseconds(match.groups()[:4]),
                        _milliseconds(match.groups()[4:]), tuple(lines[2:])))
    return cues


def check_srt(text: str, content: dict[str, Any], *, duration_sec: float | None = None) -> dict[str, Any]:
    pages, errors = expected_pages(content)
    try:
        cues = parse_srt(text)
    except ValueError as exc:
        return {"errors": [*errors, str(exc)], "cue_count": 0, "expected_page_count": len(pages)}
    if len(cues) != len(pages):
        errors.append(f"SRT cue count {len(cues)} != expected caption page count {len(pages)}")
    try:
        version = int(content.get("quality_contract_version", 1))
    except (TypeError, ValueError, OverflowError):
        errors.append("quality_contract_version is invalid")
        version = 1
    video = content.get("video")
    terms = video.get("subtitle_keep_terms", []) if isinstance(video, dict) else []
    if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
        terms = []  # expected_pages already reports malformed protection settings.
    limit_ms = None
    if duration_sec is not None:
        if not math.isfinite(duration_sec) or duration_sec <= 0:
            errors.append("media duration must be finite and positive")
        else:
            # Timestamps round to milliseconds; allow only that rounding, not a frame.
            limit_ms = math.ceil(duration_sec * 1000) + 1
    for i, cue in enumerate(cues):
        label = f"SRT cue {i + 1}"
        if cue.number != i + 1:
            errors.append(f"{label}: index must be sequential from 1")
        if cue.end_ms <= cue.start_ms:
            errors.append(f"{label}: end must be later than start")
        if i and cue.start_ms < cues[i - 1].end_ms:
            errors.append(f"{label}: overlaps or precedes previous cue")
        if limit_ms is not None and cue.end_ms > limit_ms:
            errors.append(f"{label}: extends beyond media duration")
        if version >= 2 and len(cue.lines) > 2:
            errors.append(f"{label}: more than two subtitle lines")
        if i < len(pages) and compact("".join(cue.lines)) != compact(pages[i]):
            errors.append(f"{label}: text differs from manifest caption page")
        broken = split_terms(list(cue.lines), terms)
        if broken:
            errors.append(f"{label}: protected terms split across subtitle lines: {broken}")
    return {"errors": errors, "cue_count": len(cues), "expected_page_count": len(pages),
            "scope": "sidecar_text_and_timestamps_not_audio_or_burned_pixels"}
