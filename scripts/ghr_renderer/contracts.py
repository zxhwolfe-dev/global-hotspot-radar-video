from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def safe_resolve_asset(root: Path, value: Any, *, label: str) -> Path:
    """Resolve a manifest asset and reject empty or project-escaping paths."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} path is empty")
    root = root.resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the episode root: {raw}") from exc
    return candidate


def stable_json_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def lightweight_asset_signature(path: Path) -> dict[str, Any]:
    """Describe an input cheaply enough for audio-reuse validation."""
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
