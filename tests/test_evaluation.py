from __future__ import annotations

import json
from importlib.resources import files

import pytest

from provider_network_intake import IntakeError
from provider_network_intake.evaluation import evaluate, frozen_corpus


def test_frozen_evaluation_measures_scripted_reuse_and_negative_cases(tmp_path):
    result = evaluate(tmp_path / "evaluation")
    assert result["families"] == 6 and result["deliveries"] == 24
    assert result["passed"] is True
    assert result["summary"] == {
        "fixed_heuristics_repeat_corrections": 38,
        "catalog_repeat_corrections": 0,
        "catalog_repeat_wrong_semantic_values": 0,
        "counterexamples_passed": 6,
        "counterexamples_total": 6,
        "model_invocations_attempted": 0,
    }
    assert result["model_condition"] == "not_requested"
    assert all(case["after_review"]["model_enabled"] is False for case in result["outcomes"])
    conditional = next(
        case for case in result["outcomes"] if case["family_id"] == "conditional-group-id"
    )
    assert conditional["scripted_review_preview"]["rule_errors"] == []
    versioned = next(case for case in result["outcomes"] if case["family_id"] == "document-version")
    assert versioned["cold_recognition"]["status"] == "unknown"
    assert versioned["after_review"]["recognition"] == "exact"
    assert all(case["operational_approvals"] == 0 for case in result["outcomes"])
    assert (
        json.loads((tmp_path / "evaluation/evaluation.json").read_text())["corpus_manifest"][
            "sha256"
        ]
        == result["corpus_manifest"]["sha256"]
    )
    with pytest.raises(FileExistsError):
        evaluate(tmp_path / "evaluation")


def test_corpus_tampering_cannot_silently_change_an_evaluation(tmp_path):
    source = files("provider_network_intake").joinpath("assets")
    for name in ("adaptation-v1.json", "adaptation-manifest-v1.json"):
        (tmp_path / name).write_bytes(source.joinpath(name).read_bytes())
    with (tmp_path / "adaptation-v1.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(IntakeError, match="digest mismatch"):
        frozen_corpus(tmp_path)


def test_missing_model_access_is_unavailable_not_a_fake_model_result(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = evaluate(tmp_path / "evaluation", invoke_model=True, model="configured-example")
    assert result["passed"] is True  # Deterministic engineering gate remains independent.
    assert result["model_condition"] == "unavailable"
    assert result["summary"]["model_invocations_attempted"] == 0
    assert all(case["model"]["state"] == "unavailable" for case in result["outcomes"])


def test_cold_model_context_excludes_future_review_and_expected_answers(tmp_path, monkeypatch):
    from provider_network_intake.assistance import build_request

    class ControlledProvider:
        def __init__(self, *, api_key, model):
            self.model = model

        def invoke(self, inspection, catalog, *, evidence, external_processing):
            assert external_processing
            request = build_request(inspection, catalog, evidence=evidence)
            serialized = json.dumps(request)
            assert (
                "expected_normalized" not in serialized and "reviewed_definition" not in serialized
            )
            assert catalog.revision == 1 and len(catalog.variants) == 1
            return {
                "state": "abstained",
                "proposal": None,
                "explanation": "Controlled protocol test only",
            }

    monkeypatch.setenv("OPENAI_API_KEY", "controlled-test-value")
    monkeypatch.setattr("provider_network_intake.evaluation.ResponsesProvider", ControlledProvider)
    result = evaluate(tmp_path / "evaluation", invoke_model=True, model="controlled-test-provider")
    assert result["summary"]["model_invocations_attempted"] == 6
    assert all(case["model"]["outcome"]["state"] == "abstained" for case in result["outcomes"])
