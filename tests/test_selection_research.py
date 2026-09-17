from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_selection_research.py"
spec = importlib.util.spec_from_file_location("selection_research", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    """Synthetic records; example.org is not a verified news source."""
    selection = {
        "research_contract_version": 1,
        "discovery_log": [{"route": "web", "query": "synthetic entity official release", "status": "completed"}],
        "final_selection": [{
            "topic": "Synthetic test topic", "today_delta": "Synthetic delta",
            "audience_payoff": "Understand a change", "primary_evidence": "s1",
            "visual_proof": "Explanatory diagram, not footage", "unknowns": ["Test fixture only"],
            "editorial_angle": "Compare the documented change", "candidate_origin": "web",
            "viewer_question": "What changed?", "time_basis": "Event date must be checked separately",
            "target_market": "global English", "claim_evidence": [{"claim": "Synthetic event", "kind": "fact", "source_ids": ["s1"]}],
        }],
    }
    sources = {"sources": [{
        "id": "s1", "url": "https://example.org/release", "publisher": "Synthetic publisher",
        "kind": "primary", "read_status": "full", "origin_group": "synthetic-release",
        "fetched_at": "2026-09-17T09:00:00Z",
    }]}
    return selection, sources


def test_valid_record_is_structure_only_not_publication_pass():
    selection, sources = fixture()
    result = module.check(selection, sources, require=True)
    assert result["status"] == "STRUCTURE_OK"
    assert result["publication_readiness"] == "not_assessed"


@pytest.mark.parametrize("require,status", [(False, "NOT_CONFIGURED"), (True, "FAIL")])
def test_legacy_report_is_never_mislabeled_checked(require, status):
    assert module.check({"final_selection": [{}]}, {}, require=require)["status"] == status


@pytest.mark.parametrize("version", [True, 2, "1", 1.0, None])
def test_contract_version_is_strict(version):
    selection, sources = fixture()
    selection["research_contract_version"] = version
    assert module.check(selection, sources, require=True)["status"] == "FAIL"


@pytest.mark.parametrize("selection,sources", [(None, {}), ([], {}), ({"research_contract_version": 1}, None), ({"research_contract_version": 1, "final_selection": [None]}, {"sources": [None]})])
def test_malformed_containers_return_diagnostics(selection, sources):
    assert module.check(selection, sources, require=True)["status"] == "FAIL"


@pytest.mark.parametrize("read_status", ["unread", "headline"])
def test_unread_and_headline_only_sources_cannot_support_claims(read_status):
    selection, sources = fixture()
    sources["sources"][0]["read_status"] = read_status
    assert module.check(selection, sources)["status"] == "FAIL"


def test_attention_is_not_event_confirmation():
    selection, sources = fixture()
    sources["sources"][0]["kind"] = "attention"
    assert module.check(selection, sources)["status"] == "FAIL"
    selection["final_selection"][0]["claim_evidence"][0]["kind"] = "attention"
    assert module.check(selection, sources)["status"] == "STRUCTURE_OK"


def test_inference_must_remain_distinct():
    selection, sources = fixture()
    claim = selection["final_selection"][0]["claim_evidence"][0]
    claim["kind"] = "inference"
    assert module.check(selection, sources)["status"] == "FAIL"
    claim["reasoning"] = "Synthetic reasoning for the test, not a verified conclusion"
    assert module.check(selection, sources)["status"] == "STRUCTURE_OK"


@pytest.mark.parametrize("ref", ["unknown", [], 7])
def test_invalid_source_references_fail(ref):
    selection, sources = fixture()
    selection["final_selection"][0]["claim_evidence"][0]["source_ids"] = [ref]
    assert module.check(selection, sources)["status"] == "FAIL"


def test_duplicate_source_ids_fail():
    selection, sources = fixture()
    sources["sources"].append(copy.deepcopy(sources["sources"][0]))
    assert module.check(selection, sources)["status"] == "FAIL"


def test_duplicate_url_is_not_independent_confirmation():
    selection, sources = fixture()
    other = copy.deepcopy(sources["sources"][0])
    other.update(id="s2", url=other["url"] + "#section", origin_group="another-origin")
    sources["sources"].append(other)
    result = module.check(selection, sources)
    assert result["status"] == "FAIL"
    assert result["stats"]["unique_source_urls"] == 1


def test_unknown_origin_and_excerpt_are_warnings_not_fake_verification():
    selection, sources = fixture()
    sources["sources"][0].update(origin_group=None, read_status="excerpt")
    result = module.check(selection, sources)
    assert result["status"] == "STRUCTURE_OK"
    assert len(result["warnings"]) == 2


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///secret", "https://user:password@example.org", "https://example.org:bad"])
def test_non_source_urls_and_embedded_credentials_are_rejected(url):
    selection, sources = fixture()
    sources["sources"][0]["url"] = url
    assert module.check(selection, sources)["status"] == "FAIL"


def test_missing_timezone_is_not_accepted_as_a_precise_observation():
    selection, sources = fixture()
    sources["sources"][0]["fetched_at"] = "2026-09-17T09:00:00"
    assert module.check(selection, sources)["status"] == "FAIL"


def test_failed_search_stays_visible():
    selection, sources = fixture()
    selection["discovery_log"][0]["status"] = "unavailable"
    result = module.check(selection, sources)
    assert result["warnings"]
    assert result["publication_readiness"] == "not_assessed"


def test_cli_bad_json_reports_failure_without_traceback(tmp_path):
    selection = tmp_path / "selection.json"
    sources = tmp_path / "sources.json"
    selection.write_text("{broken", encoding="utf-8")
    sources.write_text("{}", encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--selection", str(selection), "--sources", str(sources), "--require"], capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "FAIL"
    assert "Traceback" not in result.stderr


def test_cli_checks_without_modifying_inputs(tmp_path):
    selection, sources = fixture()
    a, b = tmp_path / "selection.json", tmp_path / "sources.json"
    a.write_text(json.dumps(selection), encoding="utf-8")
    b.write_text(json.dumps(sources), encoding="utf-8")
    before = a.read_bytes(), b.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), "--selection", str(a), "--sources", str(b), "--require"], capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "STRUCTURE_OK"
    assert (a.read_bytes(), b.read_bytes()) == before


@pytest.mark.parametrize("value", [{}, False, 0])
def test_empty_or_non_content_selection_values_fail(value):
    selection, sources = fixture()
    selection["final_selection"][0]["today_delta"] = value
    assert module.check(selection, sources)["status"] == "FAIL"


def test_unknown_first_origin_does_not_hide_later_conflicting_origins():
    selection, sources = fixture()
    first = sources["sources"][0]
    first["origin_group"] = None
    sources["sources"].extend([{**first, "id": "s2", "origin_group": "origin-a"}, {**first, "id": "s3", "origin_group": "origin-b"}])
    assert module.check(selection, sources)["status"] == "FAIL"


def test_url_with_whitespace_is_not_a_source_url():
    assert not module.url_key("https://example.org bad/release")
