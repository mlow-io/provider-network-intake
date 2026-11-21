"""Original synthetic recurring-roster journey. Never calls a model or touches existing data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog_store import CatalogStore
from .engine import Engine
from .exports import exceptions_csv, read_bundle, write_bundle
from .models import SourceContext
from .recipes import Variant


def run_demo(destination: str | Path) -> dict[str, Any]:
    root = Path(destination)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    engine = Engine()
    store = CatalogStore(root / "catalog.sqlite3")
    source = "synthetic-sender-a"
    a = b"Practitioner NPI,Office ID,Exclude From Directory\n1000000004,NORTH,Y\n1000000004,SOUTH,N\n"
    b = b"NPI,Site,Directory Flag\n1000000004,NORTH,Y\n1000000004,SOUTH,N\n"
    note = "Original synthetic source instruction: NPI is practitioner ID; Site is office key; Y requests directory exclusion and N requests display. Network context is not supplied."
    (root / "source-note.txt").write_text(note + "\n", encoding="utf-8")

    def definition(headers: list[str]) -> dict[str, Any]:
        return {
            "version": 1,
            "source": source,
            "contract": "payer-network-roster/1",
            "grain": "practitioner_office",
            "headers": headers,
            "mapping": dict(
                zip(["practitioner_npi", "location_id", "directory_visible"], headers, strict=True)
            ),
            "transforms": {
                "directory_visible": [{"op": "value_map", "values": {"Y": False, "N": True}}]
            },
            "evidence_refs": ["synthetic-source-note"],
            "source_notes": {"synthetic-source-note": note},
        }

    first_context = SourceContext(source, "delivery-a")
    inspection_a = engine.inspect(a, context=first_context)
    seed = Variant.from_dict(definition(list(inspection_a.fields)))
    proposal_a = store.propose(
        inspection_a, seed, origin="manual", request_id="seed-a", expected_revision=0
    )
    store.review(
        proposal_a["id"],
        actor="Synthetic reviewer",
        reason="Initial source instructions reviewed",
        expected_revision=0,
    )
    context_b = SourceContext(source, "delivery-b")
    inspection_b = engine.inspect(b, context=context_b)
    store.observe(inspection_b)
    before = engine.recognize(inspection_b, catalog=store.snapshot())
    guessed = engine.propose_adaptation(inspection_b, recognition=before, catalog=store.snapshot())
    before_report = engine.preview(b, proposal=guessed, context=context_b)
    revised = Variant.from_dict(definition(list(inspection_b.fields)))
    proposal_b = store.propose(
        inspection_b, revised, origin="manual", request_id="review-b", expected_revision=1
    )
    preview_b = engine.preview(
        b, proposal=proposal_b, context=context_b, expected_catalog_revision=1
    )
    store.review(
        proposal_b["id"],
        actor="Synthetic reviewer",
        reason="Renamed fields and exclusion polarity checked against source note",
        expected_revision=1,
    )
    # Reopen the catalog and interpret a later delivery without a model or conversation state.
    reopened = CatalogStore(root / "catalog.sqlite3")
    context_c = SourceContext(source, "delivery-c")
    inspection_c = engine.inspect(b, context=context_c)
    snapshot = reopened.snapshot()
    recognition_c = engine.recognize(inspection_c, catalog=snapshot)
    proposal_c = engine.propose_adaptation(
        inspection_c, recognition=recognition_c, catalog=snapshot
    )
    report_c = engine.preview(
        b, proposal=proposal_c, context=context_c, expected_catalog_revision=2
    )
    other = engine.inspect(b, context=SourceContext("synthetic-sender-b", "delivery-d"))
    recognition_d = engine.recognize(other, catalog=snapshot)
    for name, payload in (("delivery-a.csv", a), ("delivery-b.csv", b), ("delivery-c.csv", b)):
        (root / name).write_bytes(payload)
    (root / "before-review.json").write_text(
        json.dumps(before_report, indent=2) + "\n", encoding="utf-8"
    )
    (root / "reviewed-preview.json").write_text(
        json.dumps(preview_b, indent=2) + "\n", encoding="utf-8"
    )
    write_bundle(
        root / "delivery-c-evidence",
        report_c,
        variants=(revised,),
        history=reopened.history(),
        original=b,
    )
    imported = read_bundle(root / "delivery-c-evidence")
    (root / "exceptions.csv").write_text(exceptions_csv(report_c), encoding="utf-8")
    summary = {
        "synthetic": True,
        "model_invocations": 0,
        "before_review": before.status,
        "after_restart": recognition_c.status,
        "other_source": recognition_d.status,
        "catalog_revision": reopened.snapshot().revision,
        "north_directory_visible": report_c["rows"][0]["normalized"]["directory_visible"],
        "south_directory_visible": report_c["rows"][1]["normalized"]["directory_visible"],
        "participation_export": report_c["eligibility"]["state"],
        "import_authority": imported["import_state"],
        "notice": "Manual reviewed-variant demonstration; no actual-model quality or real-world participation claim.",
    }
    assert summary["before_review"] == "unknown" and summary["after_restart"] == "exact"
    assert summary["other_source"] == "unknown" and summary["north_directory_visible"] is False
    assert summary["south_directory_visible"] is True and summary["participation_export"] == "held"
    (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
