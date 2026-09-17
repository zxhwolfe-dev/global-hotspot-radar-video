#!/usr/bin/env python3
"""Read-only P0 research-record preflight, not a fact checker or publish gate."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def url_key(value: object) -> str:
    if not text(value) or any(c.isspace() or ord(c) < 32 for c in value):
        return ""
    try:
        p = urlsplit(value.strip())
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            return ""
        _ = p.port  # Reject malformed ports; no URL is fetched by this utility.
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))
    except ValueError:
        return ""


def aware_timestamp(value: object) -> bool:
    if not text(value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def check(selection: object, sources: object, *, require: bool = False) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    stats = {"selected_topics": 0, "linked_claims": 0, "unique_source_urls": 0}

    def result(status: str) -> dict:
        return {"status": status, "scope": "record_structure_and_references_only",
                "publication_readiness": "not_assessed", "errors": errors,
                "warnings": warnings, "stats": stats}

    if not isinstance(selection, dict):
        errors.append("selection_report must be an object")
        return result("FAIL")
    version = selection.get("research_contract_version")
    if version is None and not require:
        warnings.append("legacy report: research contract was not checked")
        return result("NOT_CONFIGURED")
    if type(version) is not int or version != 1:
        errors.append("research_contract_version must be integer 1")
        return result("FAIL")

    log = selection.get("discovery_log")
    if not isinstance(log, list) or not log:
        errors.append("discovery_log must record actual attempts, including failed/unavailable ones")
    else:
        for i, row in enumerate(log):
            if (not isinstance(row, dict) or row.get("route") not in ("radar", "web", "primary", "audience")
                    or row.get("status") not in ("completed", "failed", "unavailable") or not text(row.get("query"))):
                errors.append(f"discovery_log[{i}] needs route, query and actual status")
            elif row["status"] != "completed":
                warnings.append(f"discovery_log[{i}]: {row['route']} {row['status']}; coverage is limited")
        if not any(isinstance(r, dict) and r.get("route") in ("web", "primary") and r.get("status") == "completed" for r in log):
            warnings.append("no completed web/primary research attempt is recorded")

    rows = sources.get("sources") if isinstance(sources, dict) else None
    if not isinstance(rows, list) or not rows:
        errors.append("source_manifest.sources must be a non-empty list")
        rows = []
    by_id: dict[str, dict] = {}
    by_url: dict[str, object] = {}
    for i, row in enumerate(rows):
        prefix = f"sources[{i}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object")
            continue
        sid, key = row.get("id"), url_key(row.get("url"))
        if not text(sid) or sid in by_id:
            errors.append(f"{prefix} needs a unique non-empty id")
            continue
        by_id[sid] = row
        if not key or not text(row.get("publisher")):
            errors.append(f"{prefix} needs a publisher and an HTTP(S) source URL without credentials")
        if row.get("kind") not in ("primary", "reporting", "attention"):
            errors.append(f"{prefix} has an invalid kind")
        if row.get("read_status") not in ("full", "excerpt", "headline", "unread"):
            errors.append(f"{prefix} has an invalid read_status")
        if not aware_timestamp(row.get("fetched_at")):
            errors.append(f"{prefix}.fetched_at needs an ISO timestamp with timezone")
        group = row.get("origin_group")
        if group is not None and not text(group):
            errors.append(f"{prefix}.origin_group must be a non-empty string or null")
        if group is None:
            warnings.append(f"{prefix}: independent origin is unknown; do not count it as independent confirmation")
        if key:
            if key in by_url:
                warnings.append(f"{prefix}: repeated source URL is not a new source")
                previous = by_url[key]
                if previous is None and group is not None:
                    by_url[key] = group
                if previous is not None and group is not None and previous != group:
                    errors.append(f"{prefix}: the same URL has conflicting origin_group values")
            else:
                by_url[key] = group
    stats["unique_source_urls"] = len(by_url)

    selected = selection.get("final_selection")
    if not isinstance(selected, list) or not selected:
        errors.append("final_selection must be a non-empty list")
        selected = []
    stats["selected_topics"] = len(selected)
    required = ("topic", "today_delta", "audience_payoff", "primary_evidence", "visual_proof", "unknowns", "editorial_angle")
    for i, item in enumerate(selected):
        prefix = f"final_selection[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if any(not (text(item.get(k)) or isinstance(item.get(k), (list, dict)) and bool(item[k])) for k in required):
            errors.append(f"{prefix} is missing existing selection fields")
        if item.get("candidate_origin") not in ("radar", "web", "mixed"):
            errors.append(f"{prefix}.candidate_origin must be radar, web or mixed")
        for key in ("viewer_question", "time_basis", "target_market"):
            if not text(item.get(key)):
                errors.append(f"{prefix}.{key} must be explicit")
        claims = item.get("claim_evidence")
        if not isinstance(claims, list) or not claims:
            errors.append(f"{prefix}.claim_evidence must link specific claims to sources")
            continue
        for j, claim in enumerate(claims):
            label = f"{prefix}.claim_evidence[{j}]"
            if not isinstance(claim, dict) or not text(claim.get("claim")) or claim.get("kind") not in ("fact", "attention", "inference"):
                errors.append(f"{label} needs a claim and a valid kind")
                continue
            refs = claim.get("source_ids")
            if not isinstance(refs, list) or not refs or any(not text(s) for s in refs):
                errors.append(f"{label}.source_ids must be a non-empty string list")
                continue
            linked = []
            for sid in refs:
                source = by_id.get(sid)
                if source is None:
                    errors.append(f"{label}: unknown source id {sid}")
                else:
                    linked.append(source)
                    if source.get("read_status") not in ("full", "excerpt"):
                        errors.append(f"{label}: {sid} was not read beyond a headline")
                    elif source["read_status"] == "excerpt":
                        warnings.append(f"{label}: {sid} is excerpt-only; context needs editorial review")
            if claim["kind"] != "attention" and not any(s.get("kind") in ("primary", "reporting") for s in linked):
                errors.append(f"{label}: attention signals alone cannot support event facts or inference")
            if claim["kind"] == "inference" and not text(claim.get("reasoning")):
                errors.append(f"{label}: inference needs explicit reasoning, separate from reported facts")
            stats["linked_claims"] += 1
    return result("FAIL" if errors else "STRUCTURE_OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--require", action="store_true", help="Require the opt-in contract for a new episode")
    args = parser.parse_args()
    try:
        selection = json.loads(args.selection.read_text(encoding="utf-8"))
        sources = json.loads(args.sources.read_text(encoding="utf-8"))
        output = check(selection, sources, require=args.require)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        output = {"status": "FAIL", "scope": "record_structure_and_references_only",
                  "publication_readiness": "not_assessed", "errors": [str(exc)], "warnings": []}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 2 if output["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
