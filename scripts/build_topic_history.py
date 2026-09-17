#!/usr/bin/env python3
"""Build and query the Global Hotspot Radar topic-history index."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_CATALOGUE = Path(
    os.environ.get("GHR_WORK_ROOT", "/mnt/d/AIWorkstationData/creative_work/videos")
).expanduser().resolve()
DEFAULT_OUTPUT = DEFAULT_CATALOGUE / "global_hotspot_topic_history.json"
PROJECT_MARKER = "global_hotspot_radar"
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}


def normalize_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", value.casefold())


def normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value.casefold().rstrip("/")
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_KEYS
    ]
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), urlencode(query), ""))


def source_urls(item: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    direct_keys = ("source", "article", "supporting_article", "primary_context", "url")
    for key in direct_keys:
        value = item.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(normalize_url(value))
    nested = item.get("sources")
    if isinstance(nested, list):
        for source in nested:
            if isinstance(source, dict) and isinstance(source.get("url"), str):
                urls.append(normalize_url(source["url"]))
    return sorted({url for url in urls if url})


def source_titles(item: dict[str, Any]) -> list[str]:
    titles: list[str] = []
    for key in ("title", "topic", "topic_cn", "claim"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            titles.append(value.strip())
    nested = item.get("sources")
    if isinstance(nested, list):
        for source in nested:
            if isinstance(source, dict) and isinstance(source.get("title"), str):
                titles.append(source["title"].strip())
    return list(dict.fromkeys(titles))


def infer_language(root: Path) -> str:
    content = root / "content_manifest.json"
    if content.is_file():
        try:
            value = json.loads(content.read_text(encoding="utf-8")).get("language")
            if value:
                return str(value)
        except (OSError, json.JSONDecodeError):
            pass
    return "en" if root.name.endswith("_EN") else "zh-CN"


def infer_date(root: Path, data: dict[str, Any]) -> str:
    for key in ("created_date", "verified_at", "as_of"):
        value = data.get(key)
        if isinstance(value, str):
            match = re.search(r"\d{4}-\d{2}-\d{2}", value)
            if match:
                return match.group(0)
    match = re.search(r"\d{4}-\d{2}-\d{2}", root.name)
    return match.group(0) if match else "unknown"


def episode_events(root: Path) -> list[dict[str, Any]]:
    manifest = root / "source_manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"_error": f"{manifest}: {exc}"}]
    language = infer_language(root)
    date = infer_date(root, data)
    rows: list[dict[str, Any]] = []

    if isinstance(data.get("topic"), str) and data.get("topic", "").strip() and isinstance(data.get("sources"), list):
        groups = [(str(data["topic"]).strip(), [data])]
    elif isinstance(data.get("selection"), list):
        groups = [(str(item.get("topic") or item.get("title") or "untitled"), [item]) for item in data["selection"]]
    elif isinstance(data.get("stories"), list):
        groups = [(str(item.get("topic") or item.get("id") or item.get("claim") or "untitled"), [item]) for item in data["stories"]]
    elif isinstance(data.get("topics"), list):
        groups = [(str(item.get("topic") or item.get("id") or "untitled"), [item]) for item in data["topics"]]
    elif isinstance(data.get("sources"), list):
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in data["sources"]:
            if isinstance(item, dict):
                grouped[str(item.get("story") or item.get("topic") or item.get("title") or "untitled")].append(item)
        groups = list(grouped.items())
    else:
        return [{"_error": f"unsupported source manifest schema: {manifest}"}]

    for label, items in groups:
        urls = sorted({url for item in items for url in source_urls(item)})
        titles = list(dict.fromkeys(title for item in items for title in source_titles(item)))
        radar_ids = {str(item["radar_id"]) for item in items if item.get("radar_id")}
        for item in items:
            radar = item.get("radar")
            if isinstance(radar, dict) and radar.get("card_id"):
                radar_ids.add(str(radar["card_id"]))
        rows.append({
            "label": label,
            "normalized_label": normalize_text(label),
            "urls": urls,
            "source_titles": titles,
            "radar_ids": sorted(radar_ids),
            "appearance": {
                "date": date,
                "language": language,
                "episode_root": str(root),
                "source_manifest": str(manifest),
            },
        })
    return rows


def related(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if set(left["urls"]) & set(right["urls"]):
        return True
    if set(left["radar_ids"]) & set(right["radar_ids"]):
        return True
    a, b = left["normalized_label"], right["normalized_label"]
    if not a or not b or min(len(a), len(b)) < 5:
        return False
    return SequenceMatcher(None, a, b).ratio() >= 0.92


def merge_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    topics: list[dict[str, Any]] = []
    for event in events:
        matches = [index for index, topic in enumerate(topics) if related(event, topic)]
        if not matches:
            topics.append({
                "labels": [event["label"]],
                "normalized_label": event["normalized_label"],
                "urls": list(event["urls"]),
                "source_titles": list(event["source_titles"]),
                "radar_ids": list(event["radar_ids"]),
                "appearances": [event["appearance"]],
            })
            continue
        target = topics[matches[0]]
        target["labels"] = list(dict.fromkeys([*target["labels"], event["label"]]))
        target["urls"] = sorted(set(target["urls"]) | set(event["urls"]))
        target["source_titles"] = list(dict.fromkeys([*target["source_titles"], *event["source_titles"]]))
        target["radar_ids"] = sorted(set(target["radar_ids"]) | set(event["radar_ids"]))
        target["appearances"].append(event["appearance"])
        for extra_index in reversed(matches[1:]):
            extra = topics.pop(extra_index)
            target["labels"] = list(dict.fromkeys([*target["labels"], *extra["labels"]]))
            target["urls"] = sorted(set(target["urls"]) | set(extra["urls"]))
            target["source_titles"] = list(dict.fromkeys([*target["source_titles"], *extra["source_titles"]]))
            target["radar_ids"] = sorted(set(target["radar_ids"]) | set(extra["radar_ids"]))
            target["appearances"].extend(extra["appearances"])

    for topic in topics:
        topic["appearances"].sort(key=lambda item: (item["date"], item["language"], item["episode_root"]))
        topic["first_seen"] = topic["appearances"][0]["date"]
        topic["last_seen"] = topic["appearances"][-1]["date"]
        topic["languages"] = sorted({item["language"] for item in topic["appearances"]})
        identity = "\n".join(topic["urls"] or [topic["normalized_label"]])
        topic["topic_id"] = "ghr:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        topic.pop("normalized_label", None)
    return sorted(topics, key=lambda item: (item["last_seen"], item["labels"][0]), reverse=True)


def build(catalogue: Path) -> dict[str, Any]:
    def is_series_episode(root: Path) -> bool:
        normalized_name = re.sub(r"[-\s]+", "_", root.name.casefold())
        if PROJECT_MARKER in normalized_name:
            return True
        episode_manifest = root / "episode_manifest.json"
        try:
            episode = json.loads(episode_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return str(episode.get("series") or "").strip() == "全球热点雷达"

    roots = sorted({path.parent for path in catalogue.rglob("source_manifest.json") if is_series_episode(path.parent)})
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    for root in roots:
        for event in episode_events(root):
            if "_error" in event:
                errors.append(event["_error"])
            else:
                events.append(event)
    topics = merge_events(events)
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalogue_root": str(catalogue),
        "episodes_scanned": len(roots),
        "episode_roots": [str(root) for root in roots],
        "topic_count": len(topics),
        "errors": errors,
        "topics": topics,
    }


def query(index: dict[str, Any], text: str, url: str, limit: int) -> list[dict[str, Any]]:
    needle = normalize_text(text)
    canonical_url = normalize_url(url)
    matches: list[tuple[float, dict[str, Any], list[str]]] = []
    for topic in index.get("topics", []):
        reasons: list[str] = []
        scores: list[float] = []
        if canonical_url and canonical_url in topic.get("urls", []):
            reasons.append("exact_source_url")
            scores.append(1.0)
        if needle:
            haystacks = [normalize_text(value) for value in [*topic.get("labels", []), *topic.get("source_titles", [])]]
            for value in haystacks:
                if not value:
                    continue
                ratio = SequenceMatcher(None, needle, value).ratio()
                if needle in value or value in needle:
                    ratio = max(ratio, 0.93)
                scores.append(ratio)
            if scores and max(scores) >= 0.58:
                reasons.append("similar_title")
        if reasons:
            matches.append((max(scores), topic, reasons))
    matches.sort(key=lambda item: (item[0], item[1].get("last_seen", "")), reverse=True)
    return [
        {
            "score": round(score, 3),
            "reasons": reasons,
            "topic_id": topic["topic_id"],
            "labels": topic["labels"],
            "first_seen": topic["first_seen"],
            "last_seen": topic["last_seen"],
            "appearances": topic["appearances"],
            "matching_urls": [item for item in topic.get("urls", []) if not canonical_url or item == canonical_url],
        }
        for score, topic, reasons in matches[:limit]
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--query", default="", help="Candidate topic title to compare")
    parser.add_argument("--url", default="", help="Candidate source URL to compare")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    index = build(args.catalogue.resolve())
    if not args.no_write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result: dict[str, Any] = {
        "index": None if args.no_write else str(args.output),
        "episodes_scanned": index["episodes_scanned"],
        "topic_count": index["topic_count"],
        "errors": index["errors"],
    }
    if args.query or args.url:
        result["query"] = {"text": args.query, "url": args.url}
        result["matches"] = query(index, args.query, args.url, args.limit)
        result["repeat_warning"] = bool(result["matches"] and result["matches"][0]["score"] >= 0.90)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
