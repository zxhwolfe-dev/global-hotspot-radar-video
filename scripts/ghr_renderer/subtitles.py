from __future__ import annotations

from pathlib import Path
from typing import Any


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, centiseconds = divmod(centiseconds, 360_000)
    minutes, centiseconds = divmod(centiseconds, 6_000)
    secs, centiseconds = divmod(centiseconds, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def _visual_units(value: str) -> int:
    return sum(1 if ord(char) < 128 else 2 for char in value)


def _escape_text(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def write_single_box_ass(
    cues: list[dict[str, Any]],
    path: Path,
    *,
    width: int,
    height: int,
    font_name: str,
    font_size: float,
    margin_h: float,
    margin_v: float,
    wrapped_texts: list[str],
) -> None:
    """Write burn-in ASS with one non-overlapping backdrop per cue.

    Text and backdrop are separate events. The backdrop is one vector rectangle
    sized to the complete one- or two-line block, so line boxes can never stack
    alpha at their join.
    """
    if len(cues) != len(wrapped_texts):
        raise ValueError("wrapped subtitle text count does not match cue count")
    size = max(28.0, float(font_size))
    horizontal_limit = max(240.0, width - 2 * float(margin_h))
    pad_x = max(22.0, size * 0.52)
    pad_y = max(14.0, size * 0.30)
    line_height = size * 1.28
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font_name},{size:.1f},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0.2,0,1,0,0,5,0,0,0,1
Style: Box,{font_name},10,&H68000000,&H68000000,&H68000000,&H68000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows: list[str] = [header]
    for cue, wrapped in zip(cues, wrapped_texts):
        lines = wrapped.splitlines() or [wrapped]
        estimated_width = max(_visual_units(line) for line in lines) * size * 0.52
        box_width = min(horizontal_limit, max(size * 5.5, estimated_width + 2 * pad_x))
        box_height = len(lines) * line_height + 2 * pad_y
        left = (width - box_width) / 2
        top = height - float(margin_v) - box_height
        center_x = width / 2
        center_y = top + box_height / 2
        start = _ass_time(float(cue["start_sec"]))
        end = _ass_time(float(cue["end_sec"]))
        vector = f"{{\\an7\\pos({left:.1f},{top:.1f})\\p1\\1c&H000000&\\1a&H68&}}m 0 0 l {box_width:.1f} 0 l {box_width:.1f} {box_height:.1f} l 0 {box_height:.1f}{{\\p0}}"
        text = _escape_text(wrapped).replace("\n", r"\N")
        caption = f"{{\\an5\\pos({center_x:.1f},{center_y:.1f})}}{text}"
        rows.append(f"Dialogue: 0,{start},{end},Box,,0,0,0,,{vector}\n")
        rows.append(f"Dialogue: 1,{start},{end},Caption,,0,0,0,,{caption}\n")
    path.write_text("".join(rows), encoding="utf-8")
