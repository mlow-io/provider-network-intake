"""Portable evidence bundles carry definitions and provenance, never local approval authority."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .errors import IntakeError, ResourceLimit
from .jsonio import bytes_digest, canonical, loads, object_keys
from .models import SourceContext
from .recipes import Variant

MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_EVIDENCE_RECORD_BYTES = 16 * 1024 * 1024
ALLOWED_FILES = {"report.json", "variants.json", "history.jsonl", "original.bin", "evidence.jsonl"}


def write_bundle(
    destination: str | Path,
    report: dict[str, Any],
    *,
    variants: tuple[Variant, ...] = (),
    history: list[dict[str, Any]] | None = None,
    original: bytes | None = None,
    evidence: Iterable[dict[str, Any]] | None = None,
) -> Path:
    if report.get("format_version") != 1 or len(variants) > 1000 or len(history or []) > 1000:
        raise IntakeError("Unsupported report version or excessive catalog/history scope")
    if original is not None and bytes_digest(original) != report.get("artifact_sha256"):
        raise IntakeError("Original artifact does not match report")
    data = {
        "report.json": canonical(report).encode(),
        "variants.json": canonical(
            {
                "format_version": 1,
                "interpretations": [Variant.from_dict(v.to_dict()).to_dict() for v in variants],
            }
        ).encode(),
        "history.jsonl": b"".join(canonical(event).encode() + b"\n" for event in history or []),
    }
    if original is not None:
        data["original.bin"] = original
    if sum(map(len, data.values())) > MAX_EXPORT_BYTES:
        raise ResourceLimit("Evidence bundle exceeds 32 MiB; select a narrower explicit scope")
    root = Path(destination)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, payload in data.items():
        with (root / name).open("xb") as stream:
            stream.write(payload)
    files = {
        name: {"sha256": bytes_digest(payload), "bytes": len(payload)}
        for name, payload in data.items()
    }
    evidence_records = 0
    if evidence is not None:
        sha = hashlib.sha256()
        size = 0
        metadata_bytes = sum(map(len, data.values()))
        with (root / "evidence.jsonl").open("xb") as stream:
            for record in evidence:
                if not isinstance(record, dict) or not isinstance(record.get("locator"), str):
                    raise IntakeError("Evidence records require object values with source locators")
                encoded = canonical(record).encode() + b"\n"
                if len(encoded) > MAX_EVIDENCE_RECORD_BYTES:
                    raise ResourceLimit("A detailed evidence record exceeds 16 MiB")
                size += len(encoded)
                if size + metadata_bytes > MAX_BUNDLE_BYTES:
                    raise ResourceLimit("Streaming bundle exceeds 512 MiB")
                stream.write(encoded)
                sha.update(encoded)
                evidence_records += 1
                if evidence_records > 10_000:
                    raise ResourceLimit("Evidence exceeds 10000 source records")
        if type(report.get("row_count")) is not int or evidence_records != report["row_count"]:
            raise IntakeError("Full evidence record count does not match assessed source scope")
        files["evidence.jsonl"] = {"sha256": sha.hexdigest(), "bytes": size}
    manifest = {
        "format_version": 1,
        "producer": "provider-network-intake/0.1.0",
        "original_included": original is not None,
        "original_sha256": report.get("artifact_sha256"),
        "rows_truncated": report.get("rows_truncated", True),
        "approval_authority": "not_transferred",
        "history_scope": "caller_selected",
        "evidence_complete": evidence is not None,
        "evidence_records": evidence_records,
        "files": files,
    }
    # The manifest is written last: its absence means an interrupted, incomplete bundle.
    with (root / "manifest.json").open("x", encoding="utf-8") as stream:
        stream.write(canonical(manifest) + "\n")
    return root


def write_full_bundle(
    destination: str | Path,
    payload: bytes,
    *,
    context: SourceContext,
    proposal: dict[str, Any] | None = None,
    format_hint: str = "csv",
    history: list[dict[str, Any]] | None = None,
    include_original: bool = False,
) -> Path:
    """Assess the whole input and stream all detail, keeping summary previews bounded."""
    from .engine import Engine

    engine = Engine()
    variants: tuple[Variant, ...]
    if format_hint == "csv":
        if proposal is None:
            raise IntakeError("A pinned tabular proposal is required for full export")
        report = engine.preview(payload, proposal=proposal, context=context, detail_limit=0)
        evidence = engine.iter_evidence(payload, proposal=proposal, context=context)
        variants = (Variant.from_dict(proposal["variant"]),)
    else:
        report = engine.preview_native(
            payload, context=context, format_hint=format_hint, detail_limit=0
        )
        # Native graphs are explicitly bounded to 1000 resources and 10 MiB input.
        evidence = iter(
            engine.preview_native(
                payload, context=context, format_hint=format_hint, detail_limit=1000
            )["rows"]
        )
        variants = ()
    return write_bundle(
        destination,
        report,
        variants=variants,
        history=history,
        original=payload if include_original else None,
        evidence=evidence,
    )


def _read_file(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
                raise ResourceLimit("Bundle member must be a bounded regular file")
            data = stream.read(maximum + 1)
    except OSError as exc:
        raise IntakeError(f"Cannot read evidence bundle member: {path.name}") from exc
    if len(data) > maximum:
        raise ResourceLimit("Bundle member grew past its bound")
    return data


def read_bundle(source: str | Path) -> dict[str, Any]:
    root = Path(source)
    if root.is_symlink() or not root.is_dir():
        raise IntakeError("Bundle root must be an explicit directory, not a symlink")
    manifest = loads(_read_file(root / "manifest.json", 65536), max_bytes=65536)
    manifest = object_keys(
        manifest,
        {
            "format_version",
            "producer",
            "original_included",
            "original_sha256",
            "rows_truncated",
            "approval_authority",
            "history_scope",
            "files",
            "evidence_complete",
            "evidence_records",
        },
        {"format_version", "files", "original_included", "original_sha256"},
    )
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise IntakeError("Unsupported export major version")
    files = manifest["files"]
    if (
        not isinstance(files, dict)
        or set(files) - ALLOWED_FILES
        or {"report.json", "variants.json", "history.jsonl"} - set(files)
    ):
        raise IntakeError("Invalid bundle member set")
    payloads: dict[str, bytes] = {}
    total = 0
    metadata_total = 0
    for name, info in files.items():
        info = object_keys(info, {"sha256", "bytes"}, {"sha256", "bytes"})
        maximum = MAX_BUNDLE_BYTES if name == "evidence.jsonl" else MAX_EXPORT_BYTES
        if type(info["bytes"]) is not int or not 0 <= info["bytes"] <= maximum:
            raise ResourceLimit("Invalid bundle member length")
        total += info["bytes"]
        if total > MAX_BUNDLE_BYTES:
            raise ResourceLimit("Bundle exceeds total byte bound")
        if name == "evidence.jsonl":
            _verify_evidence(root / name, info)
            continue
        metadata_total += info["bytes"]
        if metadata_total > MAX_EXPORT_BYTES:
            raise ResourceLimit("Bundle metadata exceeds 32 MiB")
        payload = _read_file(root / name, info["bytes"])
        if len(payload) != info["bytes"] or bytes_digest(payload) != info["sha256"]:
            raise IntakeError("Bundle integrity mismatch")
        payloads[name] = payload
    report = loads(payloads["report.json"], max_bytes=MAX_EXPORT_BYTES, max_nodes=2_000_000)
    variants = loads(payloads["variants.json"], max_bytes=MAX_EXPORT_BYTES, max_nodes=2_000_000)
    if (
        not isinstance(report, dict)
        or report.get("format_version") != 1
        or not isinstance(variants, dict)
        or variants.get("format_version") != 1
    ):
        raise IntakeError("Unsupported report/variant export version")
    if report.get("artifact_sha256") != manifest["original_sha256"]:
        raise IntakeError("Report and manifest reference different artifacts")
    if type(manifest["original_included"]) is not bool or manifest["original_included"] != (
        "original.bin" in payloads
    ):
        raise IntakeError("Original inclusion marker mismatch")
    if (
        "original.bin" in payloads
        and bytes_digest(payloads["original.bin"]) != manifest["original_sha256"]
    ):
        raise IntakeError("Original artifact hash mismatch")
    definitions = variants.get("interpretations")
    if not isinstance(definitions, list) or len(definitions) > 1000:
        raise ResourceLimit("Invalid exported variant list")
    history_lines = payloads["history.jsonl"].splitlines()
    if len(history_lines) > 1000:
        raise ResourceLimit("Exported history exceeds 1000 events")
    history = [loads(line, max_bytes=65536) for line in history_lines]
    complete = manifest.get("evidence_complete", False)
    records = manifest.get("evidence_records", 0)
    if type(complete) is not bool or type(records) is not int or not 0 <= records <= 10000:
        raise IntakeError("Invalid evidence completeness/count metadata")
    if complete != ("evidence.jsonl" in files) or complete and records != report.get("row_count"):
        raise IntakeError("Full evidence scope marker does not match report")
    return {
        "manifest": manifest,
        "report": report,
        "variants": [Variant.from_dict(item) for item in definitions],
        "history": history,
        "import_state": "proposal_only",
        "original": payloads.get("original.bin"),
        "evidence_complete": complete,
        "evidence_records": records,
    }


def _verify_evidence(path: Path, info: dict[str, Any]) -> None:
    sha = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise IntakeError("Evidence member is not a regular file")
            while chunk := stream.read(65536):
                size += len(chunk)
                if size > info["bytes"]:
                    raise ResourceLimit("Detailed evidence exceeds declared byte bound")
                sha.update(chunk)
    except OSError as exc:
        raise IntakeError("Cannot read detailed evidence member") from exc
    if size != info["bytes"] or sha.hexdigest() != info["sha256"]:
        raise IntakeError("Detailed evidence integrity mismatch")


def iter_bundle_evidence(source: str | Path) -> Iterator[dict[str, Any]]:
    """Read full detail with bounded records. Fully consume before trusting final integrity."""
    imported = read_bundle(source)
    if not imported["evidence_complete"]:
        raise IntakeError("Bundle contains a bounded preview, not complete detailed evidence")
    expected = imported["manifest"]["files"]["evidence.jsonl"]
    sha = hashlib.sha256()
    count = 0
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        with os.fdopen(os.open(Path(source) / "evidence.jsonl", flags), "rb") as stream:
            for _ in range(imported["evidence_records"] + 1):
                line = stream.readline(MAX_EVIDENCE_RECORD_BYTES + 1)
                if not line:
                    break
                size += len(line)
                if len(line) > MAX_EVIDENCE_RECORD_BYTES or size > expected["bytes"]:
                    raise ResourceLimit("Detailed evidence exceeds record/byte limits")
                sha.update(line)
                record = loads(line, max_bytes=MAX_EVIDENCE_RECORD_BYTES, max_nodes=1_000_000)
                if not isinstance(record, dict) or not isinstance(record.get("locator"), str):
                    raise IntakeError("Malformed detailed evidence record")
                count += 1
                yield record
    except OSError as exc:
        raise IntakeError("Cannot read detailed evidence member") from exc
    if (
        count != imported["evidence_records"]
        or size != expected["bytes"]
        or sha.hexdigest() != expected["sha256"]
    ):
        raise IntakeError("Detailed evidence changed or has an inconsistent record count")


def exceptions_csv(report: dict[str, Any]) -> str:
    """A spreadsheet-safe projection; unmodified diagnostic values stay in JSON."""

    def safe(value: Any) -> str:
        result = str(value)
        return "'" + result if result.lstrip().startswith(("=", "+", "-", "@")) else result

    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["code", "layer", "locator", "message", "severity"])
    for item in report["findings"]["items"]:
        writer.writerow(
            [safe(item[key]) for key in ("code", "layer", "locator", "message", "severity")]
        )
    writer.writerow(
        [
            "report_scope",
            "metadata",
            "$",
            f"detailed findings truncated={report['findings']['truncated']}; total={report['findings']['total']}; approval not performed",
            "info",
        ]
    )
    return stream.getvalue()


def _md_cell(value: Any) -> str:
    return (
        str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    )


def markdown_report(
    report: dict[str, Any],
    *,
    context: SourceContext | None = None,
    recognition: dict[str, Any] | None = None,
) -> str:
    """Render a compact, zero-dependency Markdown quality and drift report."""
    ctx = report.get("context") or {}
    source = context.source if context else ctx.get("source", "unknown")
    delivery = context.delivery_id if context else ctx.get("delivery_id", "unknown")
    payer = context.payer if context else ctx.get("payer")
    network = context.network if context else ctx.get("network")
    source_version = context.source_version if context else ctx.get("source_version")
    eligibility = report.get("eligibility") or {}
    findings = report.get("findings") or {}
    counts: dict[str, int] = findings.get("counts") or {}
    items: list[dict[str, Any]] = findings.get("items") or []
    layers: dict[str, str] = report.get("layers") or {}
    uninterpreted: list[str] = report.get("uninterpreted_fields") or []

    scope_parts = [f"source=`{source}`", f"delivery=`{delivery}`"]
    if payer:
        scope_parts.append(f"payer=`{payer}`")
    if network:
        scope_parts.append(f"network=`{network}`")
    if source_version:
        scope_parts.append(f"version=`{source_version}`")

    lines = [
        "# Provider Network Intake Report",
        "",
        f"**Scope:** {' · '.join(scope_parts)}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Rows assessed | {report.get('row_count', 0)} |",
        f"| Eligibility | `{eligibility.get('state', 'unknown')}` |",
        f"| Operational approval | `{report.get('approval', 'not_performed')}` |",
        f"| Coverage | `{report.get('coverage', 'unknown')}` |",
    ]
    variant_id = report.get("variant_id") or report.get("contract")
    if variant_id:
        lines.append(f"| Variant / Contract | `{variant_id}` |")
    regime = report.get("regulatory_regime")
    if isinstance(regime, dict) and regime.get("label"):
        lines.append(f"| Regulatory regime | `{_md_cell(regime['label'])}` |")
    if recognition is not None:
        lines.append(f"| Schema recognition | `{recognition.get('status', 'unknown')}` |")
    lines.append(f"| Total findings | {findings.get('total', 0)} |")

    if layers:
        lines.extend(["", "## Layer Outcomes", "", "| Layer | Status |", "|---|---|"])
        for layer_name in sorted(layers):
            lines.append(f"| `{_md_cell(layer_name)}` | `{_md_cell(layers[layer_name])}` |")

    if counts:
        lines.extend(["", "## Findings by Code", "", "| Code | Count |", "|---|---:|"])
        for code, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])):
            lines.append(f"| `{_md_cell(code)}` | {count} |")

    if items:
        preview = items[:25]
        lines.extend(
            [
                "",
                "## Detailed Findings",
                "",
                "| Severity | Layer | Code | Locator | Message |",
                "|---|---|---|---|---|",
            ]
        )
        for item in preview:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(item.get("severity", "")),
                        f"`{_md_cell(item.get('layer', ''))}`",
                        f"`{_md_cell(item.get('code', ''))}`",
                        f"`{_md_cell(item.get('locator', ''))}`",
                        _md_cell(item.get("message", "")),
                    ]
                )
                + " |"
            )
        total_findings = findings.get("total", len(items))
        if total_findings > len(preview):
            lines.append(
                f"\n_Showing {len(preview)} of {total_findings} findings; inspect JSON or full bundle for complete details._"
            )

    if uninterpreted:
        lines.extend(["", "## Uninterpreted Fields", ""])
        for field_name in uninterpreted:
            lines.append(f"- `{_md_cell(field_name)}`")

    notice = report.get("notice")
    if notice:
        lines.extend(["", f"> {notice}"])
    return "\n".join(lines) + "\n"
