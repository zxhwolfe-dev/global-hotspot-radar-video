"""Offline regression cases taken from the September handoff failures."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from ghr_renderer.production_contracts import manifest_errors, timing_errors
from ghr_renderer.audio_contracts import default_voice_instruction, tts_fingerprint
from build_topic_history import build, normalize_text, related
from history_store import merge_history


def content():
    return {'production_contract_version': 1, 'voice': {'instruction': '中速偏快、利落流畅'},
            'cards': [{'id': 'cover', 'image': 'cover.png'},
                      {'id': 'one', 'image': 'one.png', 'tts_text': '数据对照', 'visual_anchor': '7秒与41秒'}]}


def timing(seconds):
    return [{'id': 'cover', 'start_sec': 0, 'end_sec': .35, 'duration_sec': .35},
            {'id': 'one', 'start_sec': .35, 'end_sec': .35+seconds, 'duration_sec': seconds}]


def test_voice_is_explicit_and_anchor_required():
    data = content()
    assert not manifest_errors(data)
    data['voice']['instruction'] = '舒展的自然中速'
    data['cards'][1].pop('visual_anchor')
    assert len(manifest_errors(data)) == 2
    assert '中速偏快、利落流畅' in default_voice_instruction('zh-CN')


@pytest.mark.parametrize('seconds', [12.01, 26.42, 33.22, float('nan'), float('inf'), -1])
def test_excessive_or_invalid_duration_is_blocked(seconds):
    assert timing_errors(content(), timing(seconds))


def test_twelve_seconds_and_legacy_are_supported():
    assert not timing_errors(content(), timing(12))
    data = content(); data.pop('production_contract_version')
    assert not timing_errors(data, timing(33.22))
    assert manifest_errors(data, require=True)


def test_duplicate_picture_ids_do_not_reset_hold():
    data = content()
    data['cards'].append(dict(data['cards'][1], id='two'))
    rows = timing(8) + [{'id': 'two', 'start_sec': 8.35, 'end_sec': 16.35, 'duration_sec': 8}]
    assert 'repeated static image' in str(timing_errors(data, rows))
    data['cards'][2]['image'] = 'new_evidence.png'
    assert not timing_errors(data, rows)


def test_timeline_order_and_gaps_are_rejected():
    rows = timing(4); rows[1]['id'] = 'wrong'
    assert timing_errors(content(), rows)
    rows = timing(4); rows[1]['start_sec'] += 1
    assert timing_errors(content(), rows)


def test_explicit_pace_change_invalidates_tts():
    data = content(); old = tts_fingerprint(data)
    data['voice']['instruction'] += '、句内推进感强'
    assert tts_fingerprint(data) != old


def test_history_keeps_manual_deleted_sources_metadata_and_is_idempotent(tmp_path):
    root = tmp_path/'2026-09-19_global_hotspot_radar_test'; root.mkdir()
    (root/'source_manifest.json').write_text(json.dumps({'topic':'新测试事件','sources': []}))
    old = build(tmp_path)
    old['topics'][0].update(topic_id='manual:stable', editor_note='keep this')
    old['topics'][0]['appearances'][0]['episode_root'] = ''
    result = merge_history(build(tmp_path), old, related, normalize_text)
    assert result['topics'][0]['topic_id'] == 'manual:stable'
    assert result['topics'][0]['editor_note'] == 'keep this'
    assert len(result['topics'][0]['appearances']) == 2
    again = merge_history(build(tmp_path), result, related, normalize_text)
    assert again['topics'] == result['topics']
    (root/'source_manifest.json').unlink()
    assert merge_history(build(tmp_path), again, related, normalize_text)['topics'] == again['topics']


def test_corrupt_existing_history_cannot_be_overwritten(tmp_path):
    target = tmp_path/'global_hotspot_topic_history.json'; target.write_text('{corrupt')
    run = subprocess.run([sys.executable, str(SCRIPTS/'build_topic_history.py'), '--catalogue', str(tmp_path)], capture_output=True)
    assert run.returncode != 0
    assert target.read_text() == '{corrupt'


def test_new_preflight_requires_production_without_network(tmp_path):
    from preflight_episode import check_project
    (tmp_path/'asset.png').write_bytes(b'fixture')
    data = content(); data['video'] = {'tail_hold_seconds': .8}
    for card in data['cards']: card['image'] = 'asset.png'
    data['cards'][1]['caption_text'] = data['cards'][1]['tts_text']
    (tmp_path/'content_manifest.json').write_text(json.dumps(data))
    assert check_project(tmp_path, require_production=True)['status'] == 'STRUCTURE_OK'
    data['voice']['instruction'] = '慢速'
    (tmp_path/'content_manifest.json').write_text(json.dumps(data))
    assert check_project(tmp_path, require_production=True)['status'] == 'FAIL'


def test_selected_event_is_not_split_into_its_source_documents(tmp_path):
    root = tmp_path/'2026-09-19_global_hotspot_radar_example'; root.mkdir()
    (root/'source_manifest.json').write_text(json.dumps({'sources': [
        {'id': 'a', 'title': 'official document', 'url': 'https://example.org/a'},
        {'id': 'b', 'title': 'experiment', 'url': 'https://example.org/b'}]}))
    (root/'selection_report.json').write_text(json.dumps({'final_selection': [
        {'topic': 'one editorial event', 'radar_id': 'manual-from-article'}]}))
    result = build(tmp_path)
    assert result['topic_count'] == 1
    assert result['topics'][0]['labels'] == ['one editorial event']
    assert result['topics'][0]['radar_ids'] == []
    assert len(result['topics'][0]['urls']) == 2


def test_renderer_refuses_long_cards_before_any_video_work(tmp_path, monkeypatch):
    import render_episode as renderer
    monkeypatch.setattr(renderer, 'run', lambda *a, **k: pytest.fail('must not render'))
    with pytest.raises(ValueError, match='exceeds 12s'):
        renderer.render_video(tmp_path, content(), timing(33.22), 33.57, mode='preview')
    assert not (tmp_path/'final').exists()


def test_direct_renderer_preflights_before_paid_tts(tmp_path, monkeypatch):
    import render_episode as renderer
    data = content(); data['voice']['instruction'] = 'slow'
    path = tmp_path/'content_manifest.json'; path.write_text(json.dumps(data))
    monkeypatch.setattr(sys, 'argv', ['render_episode.py', '--manifest', str(path)])
    monkeypatch.setattr(renderer, 'build_audio', lambda *a, **k: pytest.fail('must not call TTS'))
    with pytest.raises(ValueError, match='voice.instruction'):
        renderer.main()


def test_final_validation_can_require_new_contract_for_legacy_input(tmp_path):
    from validate_episode import validate
    data = content(); data.pop('production_contract_version')
    data['video'] = {'tail_hold_seconds': .8}
    (tmp_path/'content_manifest.json').write_text(json.dumps(data))
    report = validate(tmp_path, require_production=True)
    assert report['status'] == 'FAIL'
    assert any('production_contract_version' in error for error in report['errors'])


def test_final_cli_requires_production_before_external_checks(tmp_path):
    data = content(); data.pop('production_contract_version')
    data['video'] = {'tail_hold_seconds': .8}
    (tmp_path/'content_manifest.json').write_text(json.dumps(data))
    run = subprocess.run([sys.executable, str(SCRIPTS/'validate_episode.py'), str(tmp_path),
                          '--require-production'], capture_output=True, text=True)
    assert run.returncode == 1
    assert any('production_contract_version' in error for error in json.loads(run.stdout)['errors'])
