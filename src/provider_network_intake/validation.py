"""Attach explicitly supplied, hash-bound external FHIR validation evidence.

Parsing and source interpretation do not run a standards validator. This boundary
checks a caller-attributed invocation manifest and preserves its scoped diagnostics;
it never approves a dataset or changes the original parser findings.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .errors import IntakeError, ResourceLimit
from .jsonio import bytes_digest, canonical, loads, object_keys, text

OUTCOMES = {"passed", "failed", "incomplete", "not_run", "unavailable", "not_applicable"}


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise IntakeError(f"{name} requires a SHA-256 hex digest")
    return value


def attach_validation(
    report: dict[str, Any], payload: bytes, outcome_bytes: bytes, manifest: dict[str, Any]
) -> dict[str, Any]:
    """Return a new report with scoped external evidence; no in-place mutation or promotion."""
    if report.get("format_version") != 1 or report.get("source_format") != "plannet":
        raise IntakeError("External profile attachment requires a native Plan-Net report")
    keys = {
        "format_version",
        "input_sha256",
        "report_sha256",
        "validator",
        "validator_version",
        "binary_sha256",
        "fhir_version",
        "guide_version",
        "packages",
        "arguments",
        "exit_code",
        "scope",
        "profiles",
        "terminology_mode",
        "layers",
    }
    manifest = object_keys(manifest, keys, keys)
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise IntakeError("Unsupported external validation manifest version")
    input_sha = _sha(manifest["input_sha256"], "input_sha256")
    if input_sha != bytes_digest(payload) or input_sha != report.get("artifact_sha256"):
        raise IntakeError("External report applies to a different original artifact")
    if _sha(manifest["report_sha256"], "report_sha256") != bytes_digest(outcome_bytes):
        raise IntakeError("External diagnostics digest mismatch")
    _sha(manifest["binary_sha256"], "binary_sha256")
    text(manifest["validator"], "validator")
    text(manifest["validator_version"], "validator_version")
    if manifest["fhir_version"] != "4.0.1" or manifest["guide_version"] != "1.2.0":
        raise IntakeError("Validation target differs from the declared R4/Plan-Net subset")
    if not isinstance(manifest["scope"], str) or manifest["scope"] not in {
        "whole_artifact",
        "selected_resources",
    }:
        raise IntakeError("Unknown external validation scope")
    if not isinstance(manifest["terminology_mode"], str) or manifest["terminology_mode"] not in {
        "disabled",
        "offline",
        "online",
    }:
        raise IntakeError("Unknown terminology mode")
    if type(manifest["exit_code"]) is not int or not 0 <= manifest["exit_code"] <= 255:
        raise IntakeError("Invalid validator exit code")
    for name, maximum in (("arguments", 64), ("profiles", 20)):
        values = manifest[name]
        if not isinstance(values, list) or not 1 <= len(values) <= maximum:
            raise IntakeError(f"{name} must be a bounded nonempty list")
        for value in values:
            text(value, name, limit=2000)
    packages = manifest["packages"]
    if not isinstance(packages, list) or not 1 <= len(packages) <= 100:
        raise IntakeError("A pinned package inventory is required")
    for package in packages:
        package = object_keys(
            package,
            {"name", "version", "sha256", "hash_scope"},
            {"name", "version", "sha256", "hash_scope"},
        )
        text(package["name"], "package name")
        text(package["version"], "package version")
        _sha(package["sha256"], "package sha256")
        if not isinstance(package["hash_scope"], str) or package["hash_scope"] not in {
            "archive",
            "resource_tree",
            "metadata_only",
        }:
            raise IntakeError("Package hash scope must be explicit")
    layers = object_keys(manifest["layers"], {"profile", "terminology"}, {"profile", "terminology"})
    if any(not isinstance(value, str) or value not in OUTCOMES for value in layers.values()):
        raise IntakeError("Unknown external validation outcome")
    if manifest["terminology_mode"] == "disabled" and layers["terminology"] == "passed":
        raise IntakeError("Disabled terminology cannot be reported passed")
    outcome = loads(outcome_bytes, max_bytes=1024 * 1024)
    if not isinstance(outcome, dict) or outcome.get("resourceType") != "OperationOutcome":
        raise IntakeError("Expected an OperationOutcome report")
    issues = outcome.get("issue")
    if not isinstance(issues, list) or len(issues) > 1000:
        raise ResourceLimit("External report requires at most 1000 issues")
    counts = {"fatal": 0, "error": 0, "warning": 0, "information": 0}
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or not isinstance(issue.get("severity"), str)
            or issue["severity"] not in counts
        ):
            raise IntakeError("Malformed external issue severity")
        text(issue.get("code"), "issue code")
        counts[issue["severity"]] += 1
    if (counts["fatal"] or counts["error"]) and "passed" in layers.values():
        raise IntakeError("A report containing errors cannot establish an unqualified passed layer")
    if manifest["exit_code"] != 0 and layers["profile"] == "passed":
        raise IntakeError("A failed validator invocation cannot establish a passed profile check")
    result = json.loads(canonical(report))
    effective = dict(layers)
    if manifest["scope"] != "whole_artifact":
        effective = {
            layer: "incomplete" if value == "passed" else value
            for layer, value in effective.items()
        }
    result["layers"].update(effective)
    result["external_validation"] = {
        "manifest": json.loads(canonical(manifest)),
        "operation_outcome": outcome,
        "issue_counts": counts,
        "attribution": "caller_supplied_invocation",
        "scope": manifest["scope"],
        "approval": "not_performed",
    }
    # Profile evidence cannot fix unresolved native relationships or operational eligibility.
    reasons = set(result["eligibility"]["reasons"])
    if "profile_not_run" in reasons:
        reasons.remove("profile_not_run")
        if effective["profile"] != "passed":
            reasons.add(f"profile_{effective['profile']}")
    result["eligibility"]["reasons"] = sorted(reasons)
    return result  # type: ignore[no-any-return]
