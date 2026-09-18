"""Select and stage a complete, matching master-audio bundle without synthesis.

Reports written before this helper remain readable. An incomplete-write marker
makes interrupted *new* writes ineligible for reuse. This is not a transactional
publication protocol for the MP4 and every release asset.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Iterator

from .contracts import safe_resolve_asset

BUNDLE_FILES = ("timeline.json", "master_narration_with_tail.wav", "master_sync_report.json")
INCOMPLETE = ".audio-build-incomplete"
MODES = frozenset({"preview", "candidate", "release"})


def output_directory(root: Path, mode: str) -> Path:
    if mode not in MODES:
        raise ValueError(f"unsupported render mode: {mode}")
    return safe_resolve_asset(root, "final" if mode == "release" else "preview", label="audio output")


def _finite(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (positive and number <= 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'non-negative'}")
    return number


def select_bundle(
    root: Path, data: dict[str, Any], fingerprint: str, mode: str,
    duration: Callable[[Path], float],
) -> tuple[Path, list[dict[str, Any]], dict[str, Any], float]:
    """Prefer this tier, then the other tier, but only after validating a whole bundle.

    SRT/ASS are derived from the timeline and are deliberately not prerequisites.
    Invalid candidates never trigger provider synthesis or legacy hash rewriting.
    """
    preferred = output_directory(root, mode)
    alternate = output_directory(root, "preview" if mode == "release" else "release")
    failures: list[str] = []
    complete_found = False
    ids = [card.get("id") for card in data["cards"]]
    for directory in (preferred, alternate):
        try:
            if (directory / INCOMPLETE).exists():
                raise ValueError("previous audio write did not complete")
            paths = [safe_resolve_asset(root, directory / name, label="cached audio") for name in BUNDLE_FILES]
            if not all(path.is_file() for path in paths):
                raise FileNotFoundError("missing core audio bundle files")
            complete_found = True
            timeline = json.loads(paths[0].read_text(encoding="utf-8"))
            report = json.loads(paths[2].read_text(encoding="utf-8"))
            if not isinstance(report, dict) or report.get("audio_fingerprint") != fingerprint:
                raise ValueError("audio fingerprint does not match the current manifest")
            if (not isinstance(timeline, list) or not timeline
                    or any(not isinstance(row, dict) for row in timeline)
                    or [row.get("id") for row in timeline] != ids):
                raise ValueError("timeline card IDs do not match the current manifest")
            audio_sec = _finite(duration(paths[1]), "master duration", positive=True)
            _finite(report.get("narration_and_original_audio_duration_sec"), "narration duration")
            previous_end = 0.0
            for index, row in enumerate(timeline):
                start = _finite(row.get("start_sec"), "timeline start")
                end = _finite(row.get("end_sec"), "timeline end", positive=True)
                span = _finite(row.get("duration_sec"), "timeline duration", positive=True)
                if abs(start - previous_end) > 0.005 or end <= start or abs(end - start - span) > 0.005:
                    raise ValueError("timeline is not contiguous or has inconsistent durations")
                if index:
                    spoken_end = _finite(row.get("spoken_end_sec"), "spoken end", positive=True)
                    if not start < spoken_end <= end:
                        raise ValueError("spoken boundary is outside its card")
                if end > audio_sec + 0.050:
                    raise ValueError("timeline extends past the cached master audio")
                previous_end = end
            return directory, timeline, report, audio_sec
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
            failures.append(f"{directory.name}: {exc}")
    message = "--reuse-audio found no matching complete bundle; " + "; ".join(failures)
    if complete_found:
        raise ValueError(message)
    raise FileNotFoundError(message)


@contextmanager
def audio_write(directory: Path) -> Iterator[None]:
    """Mark interrupted writes unfit for reuse; do not auto-delete old artifacts."""
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / INCOMPLETE
    marker.write_text("Audio build incomplete. Reuse another matching bundle or rebuild from retained TTS.\n", encoding="utf-8")
    yield
    marker.unlink()


def stage_bundle(source: Path, target: Path) -> None:
    """Stage all core files before replacing any, then publish the report last.

    A failed copy leaves the old target untouched. Interrupted replacements leave
    an explicit marker. Individual replacements are atomic, not the whole release.
    """
    if source.resolve() == target.resolve():
        return
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".audio-stage-", dir=target) as temporary:
        staging = Path(temporary)
        for name in BUNDLE_FILES:
            shutil.copyfile(source / name, staging / name)
        with audio_write(target):
            for name in BUNDLE_FILES:
                (staging / name).replace(target / name)
