"""Lossless incremental storage for curated topic history."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path


def merge_history(current, previous, related, normalize_text):
    if not isinstance(previous, dict) or not isinstance(previous.get('topics'), list):
        raise ValueError('existing history is invalid; refusing to overwrite it')
    topics = copy.deepcopy(previous['topics'])
    for topic in topics:
        if not isinstance(topic, dict) or not topic.get('labels') or not topic.get('appearances'):
            raise ValueError('existing history contains an invalid topic; refusing to overwrite it')

    def matches(a, b):
        return any(related(dict(a, normalized_label=normalize_text(x)),
                           dict(b, normalized_label=normalize_text(y)))
                   for x in a['labels'] for y in b['labels'])

    for incoming in current['topics']:
        hits = [t for t in topics if matches(t, incoming)]
        if not hits:
            topics.append(copy.deepcopy(incoming))
            continue
        target = hits[0]
        # Do not collapse multiple manually curated topics through one ambiguous new row.
        for key in ('labels', 'urls', 'source_titles', 'radar_ids', 'appearances'):
            for value in incoming[key]:
                if value not in target[key]:
                    target[key].append(copy.deepcopy(value))
        target['appearances'].sort(key=lambda a: (a['date'], a['language'], a['episode_root']))
        target['first_seen'] = min(a['date'] for a in target['appearances'])
        target['last_seen'] = max(a['date'] for a in target['appearances'])
        target['languages'] = sorted({a['language'] for a in target['appearances']})
    result = {**previous, **current, 'topics': sorted(topics, key=lambda t: (t['last_seen'], t['labels'][0]), reverse=True)}
    result['topic_count'] = len(topics)
    result['history_mode'] = 'merge_preserving_existing_records'
    return result


def write_atomic(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def selected_groups(root: Path, sources: dict):
    """One selected event may cite many documents; documents are not episodes."""
    path = root / 'selection_report.json'
    if not path.exists():
        return None
    selection = json.loads(path.read_text(encoding='utf-8'))
    chosen = selection.get('final_selection') if isinstance(selection, dict) else None
    if not isinstance(chosen, list) or not chosen:
        return None
    rows = sources.get('sources', [])
    rows = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    groups = []
    for topic in chosen:
        if not isinstance(topic, dict) or not isinstance(topic.get('topic'), str):
            raise ValueError(f'invalid selected topic: {path}')
        ids = set()
        for claim in topic.get('claim_evidence', []):
            if isinstance(claim, dict):
                ids.update(str(v) for v in claim.get('source_ids', []))
        evidence = topic.get('primary_evidence', [])
        if isinstance(evidence, list):
            ids.update(str(v) for v in evidence if isinstance(v, str))
        linked = rows if len(chosen) == 1 else [row for row in rows if str(row.get('id')) in ids]
        record = dict(topic, sources=linked)
        # Web/manual origin markers are not Radar card IDs.
        if str(record.get('radar_id', '')).startswith(('manual', 'web')):
            record.pop('radar_id', None)
        groups.append((topic['topic'], [record]))
    return groups
