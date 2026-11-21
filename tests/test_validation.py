from __future__ import annotations

import copy
import json

import pytest

from provider_network_intake import Engine, IntakeError, SourceContext
from provider_network_intake.jsonio import bytes_digest
from provider_network_intake.validation import attach_validation


def inputs():
    payload = b'{"resourceType":"Bundle","type":"searchset","entry":[]}'
    report = Engine().preview_native(
        payload, context=SourceContext("synthetic", "one"), format_hint="plannet"
    )
    outcome = b'{"resourceType":"OperationOutcome","issue":[{"severity":"information","code":"informational","diagnostics":"Controlled fixture only"}]}'
    manifest = {
        "format_version": 1,
        "input_sha256": bytes_digest(payload),
        "report_sha256": bytes_digest(outcome),
        "validator": "controlled-test-validator",
        "validator_version": "test-1",
        "binary_sha256": "1" * 64,
        "fhir_version": "4.0.1",
        "guide_version": "1.2.0",
        "packages": [
            {"name": "synthetic", "version": "1", "sha256": "2" * 64, "hash_scope": "metadata_only"}
        ],
        "arguments": ["controlled-fixture"],
        "exit_code": 0,
        "scope": "whole_artifact",
        "profiles": ["synthetic-profile"],
        "terminology_mode": "disabled",
        "layers": {"profile": "passed", "terminology": "unavailable"},
    }
    return payload, report, outcome, manifest


def test_attachment_is_exactly_bound_and_does_not_promote_or_edit_original():
    payload, report, outcome, manifest = inputs()
    before = copy.deepcopy(report)
    attached = attach_validation(report, payload, outcome, manifest)
    assert attached["layers"]["profile"] == "passed"
    assert attached["layers"]["terminology"] == "unavailable"
    assert attached["eligibility"]["state"] == "held"
    assert attached["eligibility"]["approved"] is False
    assert attached["external_validation"]["attribution"] == "caller_supplied_invocation"
    assert (
        attached["external_validation"]["manifest"]["packages"][0]["hash_scope"] == "metadata_only"
    )
    assert attached["findings"] == before["findings"]
    assert report == before


@pytest.mark.parametrize(
    "change",
    ["input", "diagnostics", "binary", "package", "version", "disabled_tx", "exit_code", "errors"],
)
def test_mismatched_or_inconsistent_evidence_is_rejected(change):
    payload, report, outcome, manifest = inputs()
    if change == "input":
        payload += b" "
    elif change == "diagnostics":
        outcome += b" "
    elif change == "binary":
        manifest["binary_sha256"] = "not-pinned"
    elif change == "package":
        manifest["packages"] = []
    elif change == "version":
        manifest["fhir_version"] = "5.0.0"
    elif change == "disabled_tx":
        manifest["layers"]["terminology"] = "passed"
    elif change == "exit_code":
        manifest["exit_code"] = 1
    else:
        outcome = json.dumps(
            {
                "resourceType": "OperationOutcome",
                "issue": [{"severity": "error", "code": "required"}],
            }
        ).encode()
        manifest["report_sha256"] = bytes_digest(outcome)
    with pytest.raises(IntakeError):
        attach_validation(report, payload, outcome, manifest)


def test_selected_resource_check_cannot_become_a_whole_artifact_pass():
    payload, report, outcome, manifest = inputs()
    manifest["scope"] = "selected_resources"
    attached = attach_validation(report, payload, outcome, manifest)
    assert attached["layers"]["profile"] == "incomplete"
    assert attached["external_validation"]["scope"] == "selected_resources"
    assert "profile_not_run" not in attached["eligibility"]["reasons"]
    assert "profile_incomplete" in attached["eligibility"]["reasons"]
