"""Explicit local commands over the same API used by applications and agent tools."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .catalog_store import CatalogSnapshot, CatalogStore
from .demo import run_demo
from .engine import Engine
from .errors import IntakeError, ResourceLimit
from .exports import read_bundle, write_full_bundle
from .jsonio import MAX_BYTES, loads
from .models import SourceContext
from .recipes import Variant


def _read(path: Path, maximum: int = MAX_BYTES) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ResourceLimit("Input file exceeds its declared byte bound")
    return data


def _emit(value: Any, path: Path | None) -> None:
    content = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    if path is None:
        print(content, end="")
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Inspect provider/network sources and record reviewed schema variants"
    )
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("contracts", help="List supported contracts and limitations")
    demo = commands.add_parser("demo", help="Run an offline synthetic A/B/C/D review journey")
    demo.add_argument("--root", type=Path, required=True, help="New invocation-owned directory")
    evaluation = commands.add_parser(
        "evaluate", help="Run the frozen synthetic recognition/reuse experiment"
    )
    evaluation.add_argument("--root", type=Path, required=True)
    evaluation.add_argument("--invoke-model", action="store_true")
    evaluation.add_argument("--model")
    for name in ("inspect", "recognize", "propose", "preview", "export", "assist"):
        command = commands.add_parser(name)
        command.add_argument("file", type=Path)
        command.add_argument("--source", required=True)
        command.add_argument("--delivery", required=True)
        command.add_argument("--payer")
        command.add_argument("--network")
        command.add_argument("--source-version")
        command.add_argument(
            "--completeness", choices=["unknown", "partial", "full", "delta"], default="unknown"
        )
        command.add_argument("--format", choices=["csv", "nppes", "plannet"], default="csv")
        command.add_argument("--catalog", type=Path)
        command.add_argument("--output", type=Path)
        if name in {"propose", "preview", "export"}:
            command.add_argument("--definition", type=Path)
            command.add_argument("--proposal", type=Path)
        if name == "propose":
            command.add_argument("--save", action="store_true")
            command.add_argument("--request-id")
        if name == "export":
            command.add_argument("--bundle", type=Path, required=True)
            command.add_argument("--include-original", action="store_true")
        if name == "assist":
            command.add_argument(
                "--evidence",
                type=Path,
                required=True,
                help="JSON object of curated reference IDs to excerpts",
            )
            command.add_argument("--model", required=True)
            command.add_argument("--allow-external-processing", action="store_true")
            command.add_argument("--journal", type=Path, required=True)
            command.add_argument("--request-id", required=True)
    review = commands.add_parser("review")
    review.add_argument("--catalog", type=Path, required=True)
    review.add_argument("--proposal-id", required=True)
    review.add_argument("--actor", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--expected-revision", type=int, required=True)
    review.add_argument("--disposition", choices=["reviewed", "rejected"], default="reviewed")
    review.add_argument("--supersedes")
    review.add_argument("--output", type=Path)
    history = commands.add_parser("history")
    history.add_argument("--catalog", type=Path, required=True)
    history.add_argument("--limit", type=int, default=100)
    history.add_argument("--offset", type=int, default=0)
    history.add_argument("--output", type=Path)
    imported = commands.add_parser("import")
    imported.add_argument("bundle", type=Path)
    imported.add_argument("--output", type=Path)
    return root


def execute(args: argparse.Namespace) -> tuple[Any, int]:
    engine = Engine()
    if args.command == "contracts":
        return engine.contracts(), 0
    if args.command == "demo":
        return run_demo(args.root), 0
    if args.command == "evaluate":
        from .evaluation import evaluate

        result = evaluate(args.root, invoke_model=args.invoke_model, model=args.model)
        summary = {
            key: result[key]
            for key in ("passed", "summary", "families", "deliveries", "model_condition", "notice")
        }
        summary["result_file"] = "evaluation.json"
        return summary, 0 if result["passed"] and result["model_condition"] != "unavailable" else 2
    if args.command == "import":
        imported = read_bundle(args.bundle)
        return {
            "manifest": imported["manifest"],
            "variants": [v.to_dict() for v in imported["variants"]],
            "import_state": imported["import_state"],
            "notice": "Definitions require current-input checks and local review before trusted reuse.",
        }, 0
    store = CatalogStore(args.catalog) if args.catalog else None
    if args.command == "review":
        assert store is not None
        return store.review(
            args.proposal_id,
            actor=args.actor,
            reason=args.reason,
            expected_revision=args.expected_revision,
            disposition=args.disposition,
            supersedes=args.supersedes,
        ), 0
    if args.command == "history":
        assert store is not None
        return store.history(limit=args.limit, offset=args.offset), 0
    context = SourceContext(
        args.source, args.delivery, args.payer, args.network, args.completeness, args.source_version
    )
    payload = _read(args.file)
    inspection = engine.inspect(payload, context=context, format_hint=args.format)
    snapshot = store.snapshot(context.source) if store else CatalogSnapshot(0, ())
    if args.command == "inspect":
        return inspection.to_dict(), 0
    recognition = engine.recognize(inspection, catalog=snapshot)
    if args.command == "recognize":
        return recognition.to_dict(), 0
    if args.command == "assist":
        from .assistance import ResponsesProvider
        from .invocations import InvocationJournal, run_assistance

        if args.output is None:
            raise IntakeError(
                "An exclusive --output receipt path is required for external invocations"
            )
        if args.output.exists():
            raise IntakeError("Assistance output already exists; no remote call was made")
        evidence = loads(_read(args.evidence, 16384), max_bytes=16384)
        if not isinstance(evidence, dict):
            raise IntakeError("Evidence must be a JSON reference/excerpt object")
        provider = ResponsesProvider(api_key=os.environ.get("OPENAI_API_KEY", ""), model=args.model)
        run = run_assistance(
            InvocationJournal(args.journal),
            request_id=args.request_id,
            provider=provider,
            inspection=inspection,
            catalog=snapshot,
            evidence=evidence,
            external_processing=args.allow_external_processing,
        )
        return run, 0 if run["state"] == "completed" else 1
    if args.format != "csv":
        if args.command in {"propose"}:
            raise IntakeError(
                "Native source observations are inspectable; generated native parser changes are unsupported"
            )
        report = engine.preview_native(payload, context=context, format_hint=args.format)
        variant = None
    else:
        if args.proposal and args.definition:
            raise IntakeError("Choose a pinned proposal or a definition, not both")
        if args.proposal:
            proposal = loads(_read(args.proposal, 262144), max_bytes=262144)
            if (
                isinstance(proposal, dict)
                and proposal.get("state") == "completed"
                and isinstance(proposal.get("result"), dict)
            ):
                proposal = proposal["result"]
            # Model receipts wrap their validated proposal; plain proposals are also accepted.
            if isinstance(proposal, dict) and "proposal" in proposal:
                proposal = proposal["proposal"]
            if not isinstance(proposal, dict) or not isinstance(proposal.get("variant"), dict):
                raise IntakeError(
                    "Expected a completed assistance result or pinned adaptation proposal"
                )
        elif args.definition:
            variant = Variant.from_dict(loads(_read(args.definition, 262144), max_bytes=262144))
            proposal = engine.proposal(inspection, variant, catalog_revision=snapshot.revision)
        else:
            proposal = engine.propose_adaptation(
                inspection, recognition=recognition, catalog=snapshot
            )
        variant = Variant.from_dict(proposal["variant"])
        if args.command == "propose":
            if args.save:
                if store is None or not args.request_id:
                    raise IntakeError("Saving a proposal requires --catalog and --request-id")
                proposal = store.propose(
                    inspection,
                    variant,
                    origin="manual",
                    request_id=args.request_id,
                    expected_revision=snapshot.revision,
                )
            return proposal, 0
        report = engine.preview(
            payload, proposal=proposal, context=context, expected_catalog_revision=snapshot.revision
        )
    if args.command == "export":
        history = (
            [
                event
                for event in store.history(limit=1000)
                if variant and event["variant_id"] == variant.id
            ]
            if store
            else []
        )
        write_full_bundle(
            args.bundle,
            payload,
            context=context,
            proposal=proposal if args.format == "csv" else None,
            format_hint=args.format,
            history=history,
            include_original=args.include_original,
        )
    return report, 2 if report["eligibility"]["state"] == "held" else 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result, status = execute(args)
        _emit(result, getattr(args, "output", None))
        return status
    except (IntakeError, OSError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
