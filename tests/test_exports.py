import json

import pytest

from provider_network_intake import Engine, IntakeError, SourceContext, Variant
from provider_network_intake.exports import (
    exceptions_csv,
    iter_bundle_evidence,
    markdown_report,
    read_bundle,
    write_bundle,
    write_full_bundle,
)


def report():
    payload = b"Practitioner NPI,Office ID\n1000000004,NORTH\n"
    context = SourceContext("synthetic", "one")
    engine = Engine()
    proposal = engine.propose_adaptation(engine.inspect(payload, context=context))
    return payload, proposal, engine.preview(payload, proposal=proposal, context=context)


def test_bundle_roundtrip_preserves_evidence_but_does_not_transfer_approval(tmp_path):
    payload, proposal, result = report()
    path = write_bundle(
        tmp_path / "evidence",
        result,
        variants=(Variant.from_dict(proposal["variant"]),),
        original=payload,
    )
    imported = read_bundle(path)
    assert imported["report"] == result
    assert imported["original"] == payload
    assert imported["variants"][0].to_dict() == proposal["variant"]
    assert imported["import_state"] == "proposal_only"
    assert imported["manifest"]["approval_authority"] == "not_transferred"
    with pytest.raises(FileExistsError):
        write_bundle(path, result)


def test_artifact_omission_is_explicit_and_digests_detect_tampering(tmp_path):
    _, _, result = report()
    path = write_bundle(tmp_path / "evidence", result)
    assert read_bundle(path)["manifest"]["original_included"] is False
    (path / "report.json").write_text("{}")
    with pytest.raises(IntakeError, match="integrity"):
        read_bundle(path)


def test_path_traversal_and_symlink_members_are_rejected(tmp_path):
    _, _, result = report()
    path = write_bundle(tmp_path / "evidence", result)
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["files"]["../outside"] = {"bytes": 0, "sha256": "x"}
    (path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntakeError, match="member set"):
        read_bundle(path)
    del manifest["files"]["../outside"]
    (path / "manifest.json").write_text(json.dumps(manifest))
    outside = tmp_path / "outside"
    outside.write_text("private unrelated file")
    (path / "report.json").unlink()
    (path / "report.json").symlink_to(outside)
    with pytest.raises(IntakeError):
        read_bundle(path)
    assert outside.read_text() == "private unrelated file"


def test_csv_projection_is_labeled_and_spreadsheet_safe():
    _, _, result = report()
    result["findings"]["items"][0]["message"] = "=HYPERLINK(unsafe)"
    projected = exceptions_csv(result)
    assert "'=HYPERLINK" in projected
    assert "approval not performed" in projected
    assert result["findings"]["items"][0]["message"].startswith("=")


def test_streamed_bundle_preserves_every_row_beyond_preview_cap(tmp_path):
    payload = b"Practitioner NPI,Office ID,ZIP\n" + b"1000000004,NORTH,02108\n" * 201
    engine = Engine()
    context = SourceContext("synthetic", "full-evidence")
    proposal = engine.propose_adaptation(engine.inspect(payload, context=context))
    summary = engine.preview(payload, proposal=proposal, context=context)
    assert len(summary["rows"]) == 100 and summary["rows_truncated"]
    path = write_full_bundle(
        tmp_path / "complete", payload, proposal=proposal, context=context, include_original=True
    )
    imported = read_bundle(path)
    assert imported["evidence_complete"] and imported["evidence_records"] == 201
    assert imported["report"]["rows"] == []
    assert imported["report"]["row_count"] == 201
    records = list(iter_bundle_evidence(path))
    assert len(records) == 201
    assert records[-1]["locator"] == "$.records[200]"
    assert all(r["lineage"]["zip"]["original"] == "02108" for r in records)
    assert imported["report"]["eligibility"]["approved"] is False
    assert imported["import_state"] == "proposal_only"
    assert imported["original"] == payload


def test_streaming_integrity_and_record_count_are_verified(tmp_path):
    payload, proposal, result = report()
    path = write_full_bundle(
        tmp_path / "complete", payload, proposal=proposal, context=SourceContext("synthetic", "one")
    )
    with (path / "evidence.jsonl").open("ab") as stream:
        stream.write(b'{"locator":"forged"}\n')
    with pytest.raises(IntakeError):
        read_bundle(path)


def test_incomplete_stream_never_publishes_a_manifest(tmp_path):
    _, _, result = report()
    path = tmp_path / "interrupted"
    with pytest.raises(IntakeError, match="record count"):
        write_bundle(path, result, evidence=iter(()))
    assert path.exists() and not (path / "manifest.json").exists()
    with pytest.raises(IntakeError):
        read_bundle(path)


def test_full_native_bundle_keeps_resources_and_reference_metadata(tmp_path):
    payload = json.dumps(
        {
            "resourceType": "Bundle",
            "type": "searchset",
            "entry": [
                {
                    "resource": {
                        "resourceType": "Location",
                        "id": "north",
                        "address": {"postalCode": "02108"},
                    }
                }
            ],
        }
    ).encode()
    path = write_full_bundle(
        tmp_path / "native",
        payload,
        context=SourceContext("synthetic-fhir", "one"),
        format_hint="plannet",
    )
    imported = read_bundle(path)
    record = list(iter_bundle_evidence(path))[0]
    assert record["original"]["address"]["postalCode"] == "02108"
    assert record["locator"] == "$.entry[0].resource"
    assert imported["report"]["layers"]["profile"] == "not_run"


def test_preview_bundle_does_not_claim_to_have_a_full_evidence_stream(tmp_path):
    _, _, result = report()
    path = write_bundle(tmp_path / "preview", result)
    assert not read_bundle(path)["evidence_complete"]
    with pytest.raises(IntakeError, match="bounded preview"):
        list(iter_bundle_evidence(path))


def test_markdown_report_summarizes_scope_and_bounded_findings():
    payload = b"Practitioner NPI,Office ID\n1234567890,NORTH\n"
    context = SourceContext("cumberland", "nov-2026", payer="volunteer", network="ppo")
    engine = Engine()
    inspection = engine.inspect(payload, context=context)
    proposal = engine.propose_adaptation(inspection)
    result = engine.preview(payload, proposal=proposal, context=context)
    rendered = markdown_report(result, context=context, recognition={"status": "unknown"})
    assert "# Provider Network Intake Report" in rendered
    assert "source=`cumberland`" in rendered
    assert "`invalid_npi_checksum`" in rendered
    assert "| Eligibility | `held` |" in rendered
    assert "| Schema recognition | `unknown` |" in rendered
