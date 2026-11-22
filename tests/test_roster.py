from __future__ import annotations

import copy
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from provider_network_intake import (
    CatalogSnapshot,
    CatalogStore,
    Conflict,
    Engine,
    IntakeError,
    SourceContext,
    Variant,
    is_valid_npi,
)
from provider_network_intake.jsonio import loads

PAYLOAD = b"NPI,Site,Directory Flag,ZIP,Date\n1000000004,NORTH,Y,02108,11/21/2026\n"


@pytest.fixture
def context():
    return SourceContext("synthetic-sender-a", "delivery-b", payer="payer-a")


@pytest.fixture
def definition():
    return {
        "version": 1,
        "source": "synthetic-sender-a",
        "payer": "payer-a",
        "contract": "payer-network-roster/1",
        "grain": "practitioner_office",
        "headers": ["NPI", "Site", "Directory Flag", "ZIP", "Date"],
        "mapping": {
            "practitioner_npi": "NPI",
            "location_id": "Site",
            "directory_visible": "Directory Flag",
            "zip": "ZIP",
            "office_end": "Date",
        },
        "transforms": {
            "directory_visible": [{"op": "value_map", "values": {"Y": False, "N": True}}],
            "office_end": [{"op": "date", "format": "%m/%d/%Y"}],
        },
        "evidence_refs": ["synthetic-source-note-v2"],
    }


def test_preview_preserves_meaning_and_lineage(context, definition):
    original = copy.deepcopy(definition)
    engine = Engine()
    inspection = engine.inspect(PAYLOAD, context=context)
    proposal = engine.proposal(inspection, Variant.from_dict(definition), catalog_revision=0)
    report = engine.preview(PAYLOAD, proposal=proposal, context=context)
    row = report["rows"][0]
    assert row["normalized"] == {
        "practitioner_npi": "1000000004",
        "location_id": "NORTH",
        "directory_visible": False,
        "zip": "02108",
        "office_end": "2026-11-21",
    }
    assert row["lineage"]["directory_visible"]["original"] == "Y"
    assert row["lineage"]["directory_visible"]["locator"] == '$.records[0]["Directory Flag"]'
    assert row["lineage"]["office_end"]["steps"][0]["before"] == "11/21/2026"
    assert report["eligibility"]["state"] == "held"
    assert "network_scope_unresolved" in report["findings"]["counts"]
    assert "network_id" not in row["normalized"]
    assert report["approval"] == "not_performed"
    assert report["layers"]["terminology"] == "not_run"
    assert definition == original


def test_end_to_end_catalog_review_reopen_and_reuse(tmp_path, context, definition):
    engine = Engine()
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    inspection = engine.inspect(PAYLOAD, context=context)
    assert engine.recognize(inspection, catalog=store.snapshot()).status == "unknown"
    store.observe(inspection)
    proposal = store.propose(
        inspection,
        Variant.from_dict(definition),
        origin="manual",
        request_id="review-b",
        expected_revision=0,
    )
    review = store.review(
        proposal["id"],
        actor="Synthetic reviewer",
        reason="Source note distinguishes exclusion from display",
        expected_revision=0,
    )
    assert review["revision"] == 1
    reopened = CatalogStore(tmp_path / "catalog.sqlite3")
    next_context = SourceContext("synthetic-sender-a", "delivery-c", payer="payer-a")
    next_inspection = engine.inspect(PAYLOAD, context=next_context)
    snapshot = reopened.snapshot()
    recognition = engine.recognize(next_inspection, catalog=snapshot)
    assert recognition.status == "exact"
    current = engine.propose_adaptation(next_inspection, recognition=recognition, catalog=snapshot)
    report = engine.preview(
        PAYLOAD, proposal=current, context=next_context, expected_catalog_revision=1
    )
    assert report["rows"][0]["normalized"]["directory_visible"] is False
    assert len(reopened.history()) == 1
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as conn:
        with pytest.raises(sqlite3.IntegrityError, match="Immutable"):
            conn.execute("DELETE FROM variant_revision")
        stored = conn.execute("SELECT body FROM observation").fetchone()[0]
        assert (
            "1000000004" not in stored
        )  # Catalog observation contains shape/counts, not original records.


def test_same_shape_other_sender_or_payer_cannot_inherit(context, definition):
    snapshot = CatalogSnapshot(1, (Variant.from_dict(definition),))
    engine = Engine()
    for other in [
        SourceContext("other", "x", payer="payer-a"),
        SourceContext(context.source, "x", payer="payer-b"),
        SourceContext(context.source, "x", payer=context.payer, network="another-network"),
    ]:
        assert (
            engine.recognize(engine.inspect(PAYLOAD, context=other), catalog=snapshot).status
            == "unknown"
        )


def test_conflicting_variants_abstain_until_superseded(tmp_path, context, definition):
    engine, store = Engine(), CatalogStore(tmp_path / "catalog.sqlite3")
    inspection = engine.inspect(PAYLOAD, context=context)
    first = Variant.from_dict(definition)
    first_proposal = store.propose(
        inspection, first, origin="manual", request_id="a", expected_revision=0
    )
    store.review(first_proposal["id"], actor="reviewer", reason="Initial note", expected_revision=0)
    changed = copy.deepcopy(definition)
    changed["transforms"]["directory_visible"][0]["values"] = {"Y": True, "N": False}
    second = Variant.from_dict(changed)
    second_proposal = store.propose(
        inspection, second, origin="manual", request_id="b", expected_revision=1
    )
    store.review(
        second_proposal["id"],
        actor="reviewer",
        reason="Competing source interpretation",
        expected_revision=1,
    )
    assert engine.recognize(inspection, catalog=store.snapshot()).status == "ambiguous"
    # A new reviewed revision explicitly supersedes a competing meaning; neither history is edited.
    changed["evidence_refs"] = ["corrected-source-note"]
    replacement = Variant.from_dict(changed)
    proposal = store.propose(
        inspection, replacement, origin="manual", request_id="c", expected_revision=2
    )
    store.review(
        proposal["id"],
        actor="reviewer",
        reason="Resolve obsolete exclusion note",
        expected_revision=2,
        supersedes=first.id,
    )
    assert first.id not in {v.id for v in store.snapshot().variants}
    assert len(store.history()) == 3


def test_stale_and_duplicate_review_is_atomic(tmp_path, context, definition):
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    inspection = Engine().inspect(PAYLOAD, context=context)
    proposal = store.propose(
        inspection,
        Variant.from_dict(definition),
        origin="mcp",
        request_id="same",
        expected_revision=0,
    )
    assert (
        store.propose(
            inspection,
            Variant.from_dict(definition),
            origin="mcp",
            request_id="same",
            expected_revision=0,
        )["id"]
        == proposal["id"]
    )

    def review():
        try:
            store.review(
                proposal["id"], actor="reviewer", reason="Scoped evidence", expected_revision=0
            )
            return "reviewed"
        except Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(lambda _: review(), range(2))) == ["conflict", "reviewed"]
    assert len(store.history()) == 1


@pytest.mark.parametrize("change", ["payload", "context", "definition", "catalog"])
def test_preview_rejects_stale_content(context, definition, change):
    engine = Engine()
    proposal = engine.proposal(
        engine.inspect(PAYLOAD, context=context), Variant.from_dict(definition), catalog_revision=2
    )
    payload, current, expected = PAYLOAD, context, 2
    if change == "payload":
        payload += b"1000000004,SOUTH,N,02108,11/21/2026\n"
    elif change == "context":
        current = SourceContext(context.source, "another-delivery", payer=context.payer)
    elif change == "definition":
        proposal["variant"]["mapping"]["practitioner_npi"] = "Site"
    else:
        expected = 3
    with pytest.raises(Conflict):
        engine.preview(
            payload, proposal=proposal, context=current, expected_catalog_revision=expected
        )


def test_truncation_preserves_all_blockers_and_counts(context, definition):
    payload = b"NPI,Site,Directory Flag,ZIP,Date\n" + b"1000000004,NORTH,?,02108,99/99/9999\n" * 300
    engine = Engine()
    proposal = engine.proposal(
        engine.inspect(payload, context=context), Variant.from_dict(definition), catalog_revision=0
    )
    report = engine.preview(
        payload, proposal=proposal, context=context, detail_limit=0, finding_limit=0
    )
    assert report["rows"] == [] and report["rows_truncated"]
    assert report["findings"]["items"] == [] and report["findings"]["truncated"]
    assert report["findings"]["counts"]["invalid_transform"] == 600
    assert report["eligibility"]["state"] == "held"


def test_conditional_source_rule_correction_preserves_original_report(context, definition):
    engine = Engine()
    payload = PAYLOAD.replace(b"ZIP,Date", b"ZIP,Date,Entity Class").replace(
        b"11/21/2026\n", b"11/21/2026,SOLO\n"
    )
    definition["headers"].append("Entity Class")
    definition["mapping"]["entity_class"] = "Entity Class"
    wrong = copy.deepcopy(definition)
    wrong["required"] = ["group_npi"]
    inspection = engine.inspect(payload, context=context)
    original = engine.preview(
        payload,
        proposal=engine.proposal(inspection, Variant.from_dict(wrong), catalog_revision=0),
        context=context,
    )
    fixed = copy.deepcopy(definition)
    fixed["required_when"] = [
        {"field": "entity_class", "equals": "group", "require": ["group_npi"]}
    ]
    corrected = engine.preview(
        payload,
        proposal=engine.proposal(inspection, Variant.from_dict(fixed), catalog_revision=0),
        context=context,
    )
    assert original["findings"]["counts"]["required"] == 1
    assert "required" not in corrected["findings"]["counts"]
    assert original["variant_id"] != corrected["variant_id"]
    assert original["findings"]["counts"]["required"] == 1


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'{"x":Infinity}'])
def test_strict_json(raw):
    with pytest.raises(IntakeError):
        loads(raw)


@pytest.mark.parametrize(
    "raw",
    [
        b"NPI,NPI\na,b\n",
        b"NPI,Site\na\n",
        b"NPI,Site\na,b,c\n",
        b"NPI,\na,b\n",
        b'Name\n"unterminated\n',
    ],
)
def test_csv_rejects_ambiguous_records(context, raw):
    with pytest.raises(IntakeError):
        Engine().inspect(raw, context=context)


@pytest.mark.parametrize(
    "op",
    [
        {"op": "eval", "value": "1+1"},
        {"op": "value_map", "values": {"Y": True}},
        {"op": "date", "format": "auto"},
    ],
)
def test_recipe_rejects_unsafe_or_unsupported_operations(definition, op):
    definition["transforms"]["office_end"] = [op]
    if op["op"] == "value_map":
        definition["evidence_refs"] = []
    with pytest.raises(IntakeError):
        Variant.from_dict(definition)


def test_heuristics_abstain_on_unqualified_npi_and_directory_flag(context):
    engine = Engine()
    inspection = engine.inspect(PAYLOAD, context=context)
    proposal = engine.propose_adaptation(inspection)
    assert "practitioner_npi" not in proposal["variant"]["mapping"]
    assert "directory_visible" not in proposal["variant"]["mapping"]
    report = engine.preview(PAYLOAD, proposal=proposal, context=context)
    assert report["findings"]["counts"]["required"] == 1


def test_reordered_headers_match_but_new_mapped_header_needs_adaptation(context, definition):
    engine = Engine()
    snapshot = CatalogSnapshot(1, (Variant.from_dict(definition),))
    reordered = b"ZIP,NPI,Directory Flag,Date,Site\n02108,1000000004,Y,11/21/2026,NORTH\n"
    assert (
        engine.recognize(engine.inspect(reordered, context=context), catalog=snapshot).status
        == "exact"
    )
    renamed = PAYLOAD.replace(b"Directory Flag", b"Exclude")
    result = engine.recognize(engine.inspect(renamed, context=context), catalog=snapshot)
    assert result.status == "unknown"
    assert result.candidates[0]["match"] == "needs_adaptation"


def test_library_import_does_not_load_optional_frameworks():
    import subprocess
    import sys

    script = "import sys; import provider_network_intake; assert not ({'mcp','pandas','fastapi','openai'} & sys.modules.keys())"
    subprocess.run([sys.executable, "-c", script], check=True)


def test_unrelated_database_is_not_adopted(tmp_path):
    path = tmp_path / "other.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE business_records(id TEXT)")
    with pytest.raises(IntakeError, match="Not a"):
        CatalogStore(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ("business_records",)
        ]


def test_declared_source_version_change_requires_review_even_with_identical_headers(
    context, definition
):
    from dataclasses import replace

    definition["source_version"] = "source-guide-v1"
    original = Variant.from_dict(definition)
    engine = Engine()
    newer = replace(context, source_version="source-guide-v2")
    inspection = engine.inspect(PAYLOAD, context=newer)
    recognition = engine.recognize(inspection, catalog=CatalogSnapshot(1, (original,)))
    assert recognition.status == "unknown"
    assert recognition.selected_id is None
    assert recognition.candidates[0]["match"] == "source_version_changed"
    with pytest.raises(IntakeError, match="scope"):
        engine.proposal(inspection, original, catalog_revision=1)


def test_uninterpreted_added_fields_block_eligibility_until_reviewed(context, definition):
    from dataclasses import replace

    payload = PAYLOAD.replace(b"ZIP,Date", b"ZIP,Date,Termination Action").replace(
        b"11/21/2026\n", b"11/21/2026,UNKNOWN\n"
    )
    engine = Engine()
    current = replace(context, network="declared-network")
    definition.update(
        network=current.network, headers=definition["headers"] + ["Termination Action"]
    )
    inspection = engine.inspect(payload, context=current)
    report = engine.preview(
        payload,
        proposal=engine.proposal(inspection, Variant.from_dict(definition), catalog_revision=0),
        context=current,
    )
    assert report["coverage"] == "partial"
    assert report["uninterpreted_fields"] == ["Termination Action"]
    assert report["findings"]["counts"]["uninterpreted_field"] == 1
    assert report["eligibility"]["state"] == "held"


def test_iso_shaped_but_invalid_calendar_date_remains_a_finding(context, definition):
    definition["transforms"]["office_end"] = []
    payload = PAYLOAD.replace(b"11/21/2026", b"2026-99-99")
    engine = Engine()
    report = engine.preview(
        payload,
        proposal=engine.proposal(
            engine.inspect(payload, context=context),
            Variant.from_dict(definition),
            catalog_revision=0,
        ),
        context=context,
    )
    assert report["findings"]["counts"]["invalid_calendar_date"] == 1
    assert report["rows"][0]["lineage"]["office_end"]["original"] == "2026-99-99"


@pytest.mark.parametrize("bad", [False, "0", -1, [], None])
def test_catalog_revision_is_an_explicit_integer(tmp_path, context, definition, bad):
    store, engine = CatalogStore(tmp_path / "catalog.sqlite3"), Engine()
    inspection = engine.inspect(PAYLOAD, context=context)
    with pytest.raises(IntakeError, match="nonnegative integer"):
        store.propose(
            inspection,
            Variant.from_dict(definition),
            origin="manual",
            request_id="bad",
            expected_revision=bad,
        )
    with pytest.raises(IntakeError, match="nonnegative integer"):
        store.review("absent", actor="Reviewer", reason="Checked source", expected_revision=bad)
    assert store.snapshot().revision == 0


def test_cms_npi_luhn_checksum_validation(context, definition):
    assert is_valid_npi("1000000004") is True
    assert is_valid_npi("1990001012") is True
    assert is_valid_npi("1000000005") is False
    assert is_valid_npi("1234567890") is False
    assert is_valid_npi("12345") is False

    payload = (
        b"NPI,Site,Directory Flag,ZIP,Date\n"
        b"1990001012,NORTH,N,37203,11/21/2026\n"
        b"1000000005,SOUTH,Y,37067,11/21/2026\n"
        b"1234567890,WEST,N,37027,11/21/2026\n"
    )
    engine = Engine()
    inspection = engine.inspect(payload, context=context)
    proposal = engine.proposal(inspection, Variant.from_dict(definition), catalog_revision=0)
    report = engine.preview(payload, proposal=proposal, context=context)
    assert report["findings"]["counts"]["invalid_npi_checksum"] == 2
    assert "invalid_npi_shape" not in report["findings"]["counts"]
    assert report["eligibility"]["state"] == "held"
