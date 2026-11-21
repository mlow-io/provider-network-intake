"""Frozen synthetic recognition/adaptation evaluation with explicit review-stage boundaries."""

from __future__ import annotations

import json
import os
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from .assistance import ResponsesProvider
from .catalog_store import CatalogStore
from .engine import Engine
from .errors import IntakeError
from .invocations import InvocationJournal, run_assistance
from .jsonio import bytes_digest, loads
from .models import SourceContext
from .recipes import Variant


def frozen_corpus(directory: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    assets = (
        directory if directory is not None else files("provider_network_intake").joinpath("assets")
    )
    manifest = loads(assets.joinpath("adaptation-manifest-v1.json").read_bytes())
    if not isinstance(manifest, dict) or manifest.get("file") != "adaptation-v1.json":
        raise IntakeError("Unknown evaluation manifest")
    raw = assets.joinpath("adaptation-v1.json").read_bytes()
    if bytes_digest(raw) != manifest.get("sha256"):
        raise IntakeError("Frozen evaluation corpus digest mismatch")
    corpus = loads(raw)
    families = corpus.get("families") if isinstance(corpus, dict) else None
    if (
        not isinstance(families, list)
        or len(families) != manifest.get("families")
        or len(families) * 4 != manifest.get("deliveries")
    ):
        raise IntakeError("Frozen corpus family/delivery counts disagree")
    identifiers = [item.get("id") for item in families if isinstance(item, dict)]
    if (
        len(identifiers) != len(families)
        or any(
            not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", key)
            for key in identifiers
        )
        or len(set(identifiers)) != len(identifiers)
    ):
        raise IntakeError("Evaluation family IDs must be bounded unique slugs")
    return manifest, corpus


def _context(case: dict[str, Any], stage: str) -> SourceContext:
    data = case[stage]
    return SourceContext(
        data.get("source", case["source"]),
        f"{case['id']}-{stage}",
        completeness=data.get("completeness", "unknown"),
        source_version=data.get("source_version"),
    )


def _score(
    proposal: dict[str, Any], report: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    expected = Variant.from_dict(case["reviewed_definition"]).to_dict()
    observed = proposal["variant"]
    bindings = []
    transforms = []
    for field in sorted(set(expected["mapping"]) | set(observed["mapping"])):
        if expected["mapping"].get(field) != observed["mapping"].get(field):
            bindings.append(field)
        if expected["transforms"].get(field, []) != observed["transforms"].get(field, []):
            transforms.append(field)
    rules = [
        key for key in ("grain", "required", "required_when") if expected[key] != observed[key]
    ]
    actual = report["rows"][0]["normalized"] if report["rows"] else {}
    cleaned = [
        field for field, value in case["expected_normalized"].items() if actual.get(field) != value
    ]
    return {
        "binding_errors": bindings,
        "transform_errors": transforms,
        "rule_errors": rules,
        "cleaned_value_errors": cleaned,
        "corrections_required": len(bindings) + len(transforms) + len(rules),
        "expected_cleaned_values": len(case["expected_normalized"]),
        "wrong_semantic_values": [
            field for field in cleaned if field in actual and actual[field] not in (None, "")
        ],
        "notice": "Script-scored field/rule differences, not observed human review time.",
    }


def evaluate(
    root: str | Path,
    *,
    corpus_directory: Path | None = None,
    invoke_model: bool = False,
    model: str | None = None,
) -> dict[str, Any]:
    manifest, corpus = frozen_corpus(corpus_directory)
    destination = Path(root)
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    engine = Engine()
    provider = (
        ResponsesProvider(api_key=os.environ["OPENAI_API_KEY"], model=model)
        if invoke_model and model and os.environ.get("OPENAI_API_KEY")
        else None
    )
    journal = InvocationJournal(destination / "model-invocations.sqlite3") if provider else None
    outcomes = []
    for case in corpus["families"]:
        store = CatalogStore(destination / (case["id"] + ".sqlite3"))
        seed_context = _context(case, "seed")
        seed_payload = case["seed"]["csv"].encode()
        seed_inspection = engine.inspect(seed_payload, context=seed_context)
        seed = Variant.from_dict(case["seed"]["definition"])
        proposed_seed = store.propose(
            seed_inspection, seed, origin="manual", request_id="seed", expected_revision=0
        )
        store.review(
            proposed_seed["id"],
            actor="Scripted synthetic reviewer",
            reason="Frozen seed interpretation; not an observed human task",
            expected_revision=0,
        )
        changed_context = _context(case, "changed")
        payload = case["changed"]["csv"].encode()
        inspection = engine.inspect(payload, context=changed_context)
        snapshot = store.snapshot(case["source"])
        recognition = engine.recognize(inspection, catalog=snapshot)
        baseline = engine.propose_adaptation(inspection)
        deterministic = engine.propose_adaptation(
            inspection, recognition=recognition, catalog=snapshot
        )
        modes: dict[str, Any] = {}
        for name, proposal in (("fixed_heuristics", baseline), ("reviewed_catalog", deterministic)):
            report = engine.preview(payload, proposal=proposal, context=changed_context)
            modes[name] = {"proposal": proposal, "score": _score(proposal, report, case)}
        model_outcome: dict[str, Any] = {"state": "not_requested", "invocations_attempted": 0}
        if invoke_model:
            model_outcome["state"] = "unavailable" if provider is None else "requested"
            if provider and journal:
                run = run_assistance(
                    journal,
                    request_id=case["id"] + "-cold",
                    provider=provider,
                    inspection=inspection,
                    catalog=snapshot,
                    evidence=case["evidence"],
                    external_processing=True,
                )
                model_outcome = {
                    "state": run["state"],
                    "request_id": run["request_id"],
                    "invocations_attempted": 1,
                }
                result = run.get("result")
                if isinstance(result, dict) and result.get("proposal"):
                    proposal = result["proposal"]
                    report = engine.preview(payload, proposal=proposal, context=changed_context)
                    model_outcome.update(
                        proposal=proposal,
                        score=_score(proposal, report, case),
                        receipt=result.get("receipt"),
                    )
                elif isinstance(result, dict):
                    model_outcome["outcome"] = result
        # The gold review occurs only AFTER cold proposals are retained and scored.
        reviewed = Variant.from_dict(case["reviewed_definition"])
        reviewed_proposal = store.propose(
            inspection, reviewed, origin="manual", request_id="scripted-review", expected_revision=1
        )
        reviewed_report = engine.preview(
            payload, proposal=reviewed_proposal, context=changed_context
        )
        store.review(
            reviewed_proposal["id"],
            actor="Scripted synthetic reviewer",
            reason="Frozen scripted correction after cold evaluation; no human time claim",
            expected_revision=1,
        )
        reopened = CatalogStore(store.path)
        later_context = _context(case, "repeat")
        later_payload = case["repeat"]["csv"].encode()
        later_inspection = engine.inspect(later_payload, context=later_context)
        later_snapshot = reopened.snapshot(case["source"])
        later_match = engine.recognize(later_inspection, catalog=later_snapshot)
        later_proposal = engine.propose_adaptation(
            later_inspection, recognition=later_match, catalog=later_snapshot
        )
        later_report = engine.preview(later_payload, proposal=later_proposal, context=later_context)
        no_catalog_proposal = engine.propose_adaptation(later_inspection)
        no_catalog_report = engine.preview(
            later_payload, proposal=no_catalog_proposal, context=later_context
        )
        counter_context = _context(case, "counter")
        counter_payload = case["counter"]["csv"].encode()
        counter_inspection = engine.inspect(counter_payload, context=counter_context)
        counter_match = engine.recognize(counter_inspection, catalog=later_snapshot)
        counter_ok = counter_match.status == case["counter"]["expected_status"]
        expected_finding = case["counter"].get("expected_finding")
        counter_counts = {}
        if expected_finding:
            negative_proposal = engine.propose_adaptation(
                counter_inspection, recognition=counter_match, catalog=later_snapshot
            )
            negative_report = engine.preview(
                counter_payload, proposal=negative_proposal, context=counter_context
            )
            counter_counts = negative_report["findings"]["counts"]
            counter_ok &= (
                expected_finding in counter_counts
                and negative_report["eligibility"]["state"] == "held"
            )
        outcome = {
            "family_id": case["id"],
            "focus": case["focus"],
            "cold_recognition": recognition.to_dict(),
            "cold_modes": modes,
            "model": model_outcome,
            "scripted_review_preview": _score(reviewed_proposal, reviewed_report, case),
            "after_review": {
                "recognition": later_match.status,
                "model_enabled": False,
                "catalog": _score(later_proposal, later_report, case),
                "fixed_heuristics": _score(no_catalog_proposal, no_catalog_report, case),
            },
            "counterexample": {
                "expected": case["counter"]["expected_status"],
                "observed": counter_match.status,
                "findings": counter_counts,
                "passed": counter_ok,
            },
            "catalog_reviews": len(reopened.history()),
            "operational_approvals": 0,
        }
        outcomes.append(outcome)
    baseline_corrections = sum(
        o["after_review"]["fixed_heuristics"]["corrections_required"] for o in outcomes
    )
    catalog_corrections = sum(
        o["after_review"]["catalog"]["corrections_required"] for o in outcomes
    )
    semantic_errors = sum(
        len(o["after_review"]["catalog"]["wrong_semantic_values"]) for o in outcomes
    )
    counters = sum(o["counterexample"]["passed"] for o in outcomes)
    passed = (
        catalog_corrections < baseline_corrections
        and semantic_errors == 0
        and counters == len(outcomes)
        and all(o["after_review"]["recognition"] == "exact" for o in outcomes)
    )
    result = {
        "format_version": 1,
        "corpus_manifest": manifest,
        "synthetic": True,
        "families": len(outcomes),
        "deliveries": manifest["deliveries"],
        "outcomes": outcomes,
        "summary": {
            "fixed_heuristics_repeat_corrections": baseline_corrections,
            "catalog_repeat_corrections": catalog_corrections,
            "catalog_repeat_wrong_semantic_values": semantic_errors,
            "counterexamples_passed": counters,
            "counterexamples_total": len(outcomes),
            "model_invocations_attempted": sum(
                o["model"]["invocations_attempted"] for o in outcomes
            ),
        },
        "model_condition": "configured"
        if provider
        else "unavailable"
        if invoke_model
        else "not_requested",
        "passed": passed,
        "notice": "Frozen synthetic engineering experiment with scripted reviews; no population accuracy, actual-model benefit without real calls, or human time-saving claim.",
    }
    with (destination / "evaluation.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result
