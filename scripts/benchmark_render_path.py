#!/usr/bin/env python3
"""Benchmark available local H.264 paths for the Global Hotspot renderer."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def encoder_names() -> set[str]:
    result = run("ffmpeg", "-hide_banner", "-encoders")
    names: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return names


def gpu_report() -> dict[str, Any]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return {"available": False, "reason": "nvidia-smi_not_found"}
    result = run(
        binary,
        "--query-gpu=name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
        check=False,
    )
    return {
        "available": result.returncode == 0,
        "rows": [line.strip() for line in result.stdout.splitlines() if line.strip()],
        "error": result.stderr.strip() or None,
    }


def benchmark(output_dir: Path, seconds: float) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    encoders = encoder_names()
    profiles = [
        ("libx264_ultrafast", "libx264", ["-preset", "ultrafast", "-crf", "23"]),
        ("libx264_veryfast", "libx264", ["-preset", "veryfast", "-crf", "20"]),
        ("libx264_medium", "libx264", ["-preset", "medium", "-crf", "17"]),
        ("h264_nvenc", "h264_nvenc", ["-preset", "p5", "-cq", "20"]),
        ("h264_qsv", "h264_qsv", ["-global_quality", "20"]),
        ("h264_vaapi", "h264_vaapi", ["-qp", "20"]),
    ]
    results: list[dict[str, Any]] = []
    for label, encoder, options in profiles:
        if encoder not in encoders:
            results.append({"profile": label, "encoder": encoder, "status": "UNAVAILABLE"})
            continue
        output = output_dir / f"{label}.mp4"
        command = [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            f"testsrc2=size=1080x1920:rate=30:duration={seconds:.3f}",
            "-an", "-c:v", encoder, *options, "-pix_fmt", "yuv420p", str(output),
        ]
        started = time.perf_counter()
        result = run(*command, check=False)
        elapsed = time.perf_counter() - started
        if result.returncode:
            results.append({
                "profile": label, "encoder": encoder, "status": "FAILED",
                "elapsed_sec": round(elapsed, 3), "error": result.stderr[-1000:],
            })
            continue
        results.append({
            "profile": label, "encoder": encoder, "status": "PASS",
            "elapsed_sec": round(elapsed, 3),
            "real_time_factor": round(elapsed / seconds, 3),
            "output_bytes": output.stat().st_size,
        })
    passed = [item for item in results if item["status"] == "PASS"]
    fastest = min(passed, key=lambda item: item["elapsed_sec"]) if passed else None
    ffmpeg_version = run("ffmpeg", "-version").stdout.splitlines()[0]
    return {
        "status": "PASS" if passed else "FAIL",
        "ffmpeg": {"path": shutil.which("ffmpeg"), "version": ffmpeg_version},
        "gpu": gpu_report(),
        "seconds_per_profile": seconds,
        "results": results,
        "fastest_supported": None if fastest is None else fastest["profile"],
        "release_policy": (
            "Hardware encoding is used only when the installed FFmpeg exposes a tested encoder. "
            "Otherwise preview/candidate/release stay on libx264 with separate quality presets."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ghr_encoder_benchmark_") as temp:
        report = benchmark(Path(temp), max(0.5, args.seconds))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
