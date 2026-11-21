from __future__ import annotations

import copy
import sqlite3

import pytest

from provider_network_intake import CatalogStore, Conflict, IntakeError
from provider_network_intake.jsonio import bytes_digest
from provider_network_intake.workbench import WorkbenchBridge, mount


class Host:
    def __init__(self, root):
        self.root = root
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        self.path = root / "host.sqlite3"
        with self.db() as conn:
            conn.execute("CREATE TABLE approved_dataset (id TEXT PRIMARY KEY, bytes BLOB)")
            conn.execute(
                "INSERT INTO approved_dataset VALUES ('previous',?)", (b"previous approved export",)
            )
        self.receipts = {}

    def receive(self, receipt_id, attempt, payload):
        sha = bytes_digest(payload)
        (self.artifacts / sha).write_bytes(payload)
        self.receipts[receipt_id] = {
            "label": "synthetic.csv",
            "source": "synthetic",
            "artifact_sha": sha,
            "attempt_id": attempt,
            "attempts": [{"spec": {}}],
        }

    def detail(self, receipt_id):
        return copy.deepcopy(self.receipts[receipt_id])

    def db(self):
        return sqlite3.connect(self.path)


def setup(root):
    host = Host(root)
    host.receive("b", "attempt-b", b"NPI,Site,Directory Flag\n1000000004,NORTH,Y\n")
    catalog = CatalogStore(root / "catalog.sqlite3")
    bridge = WorkbenchBridge(host, catalog)
    inspected = bridge.inspect("b", "attempt-b")
    definition = inspected["draft"]
    definition.update(
        grain="practitioner_office",
        mapping={
            "practitioner_npi": "NPI",
            "location_id": "Site",
            "directory_visible": "Directory Flag",
        },
        transforms={"directory_visible": [{"op": "value_map", "values": {"Y": False, "N": True}}]},
        evidence_refs=["source-note"],
        source_notes={
            "source-note": "Original synthetic note: NPI is practitioner, Site office key and Y requests exclusion."
        },
    )
    return host, catalog, bridge, definition


def test_host_bridge_reviews_reuses_and_preserves_operational_approval(tmp_path):
    host, catalog, bridge, definition = setup(tmp_path)
    before = host.path.read_bytes()
    preview = bridge.preview("b", "attempt-b", definition, 0)
    assert preview["report"]["rows"][0]["normalized"]["directory_visible"] is False
    assert preview["report"]["eligibility"]["state"] == "held"
    proposal = bridge.propose("b", "attempt-b", definition, 0, preview["preview_token"], "review-b")
    assert bridge.inspect("b", "attempt-b")["pending"][0]["id"] == proposal["id"]
    bridge.review(
        "b",
        "attempt-b",
        proposal["id"],
        actor="Synthetic reviewer",
        reason="Checked source meanings",
        expected_catalog_revision=0,
        preview_token=preview["preview_token"],
    )
    reopened = WorkbenchBridge(host, CatalogStore(catalog.path))
    host.receive("c", "attempt-c", b"NPI,Site,Directory Flag\n1000000004,NORTH,N\n")
    assert reopened.inspect("c", "attempt-c")["recognition"]["status"] == "exact"
    assert host.path.read_bytes() == before
    with host.db() as conn:
        assert (
            conn.execute("SELECT bytes FROM approved_dataset WHERE id='previous'").fetchone()[0]
            == b"previous approved export"
        )


def test_stale_attempt_or_preview_cannot_record_a_variant(tmp_path):
    host, catalog, bridge, definition = setup(tmp_path)
    with pytest.raises(Conflict):
        bridge.propose("b", "attempt-b", definition, 0, "not-a-preview", "invalid")
    preview = bridge.preview("b", "attempt-b", definition, 0)
    proposal = bridge.propose("b", "attempt-b", definition, 0, preview["preview_token"], "valid")
    host.receipts["b"]["attempt_id"] = "new-attempt"
    with pytest.raises(Conflict):
        bridge.review(
            "b",
            "attempt-b",
            proposal["id"],
            actor="reviewer",
            reason="Stale review",
            expected_catalog_revision=0,
            preview_token=preview["preview_token"],
        )
    assert catalog.history() == [] and catalog.snapshot().variants == ()


def test_pending_proposal_must_belong_to_this_retained_attempt(tmp_path):
    host, _, bridge, definition = setup(tmp_path)
    preview = bridge.preview("b", "attempt-b", definition, 0)
    proposal = bridge.propose("b", "attempt-b", definition, 0, preview["preview_token"], "valid")
    host.receive("c", "attempt-c", b"NPI,Site,Directory Flag\n1000000004,NORTH,Y\n")
    assert bridge.inspect("c", "attempt-c")["pending"] == []
    with pytest.raises(Conflict):
        bridge.proposal("c", "attempt-c", proposal["id"])


def test_optional_http_mount_uses_typed_failures_and_current_preview(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    host, catalog, _, definition = setup(tmp_path)
    app = FastAPI()
    mount(app, host, catalog)
    client = TestClient(app)
    base = "/api/intakes/b/source-schema"
    assert (
        client.post(
            base + "/inspect", json={"expected_attempt": "attempt-b", "unknown": True}
        ).status_code
        == 422
    )
    assert client.post(base + "/inspect", json={"expected_attempt": "stale"}).status_code == 409
    preview = client.post(
        base + "/preview",
        json={
            "expected_attempt": "attempt-b",
            "expected_catalog_revision": 0,
            "definition": definition,
        },
    )
    assert preview.status_code == 200
    assert preview.json()["report"]["approval"] == "not_performed"


def test_artifact_integrity_is_checked_before_source_interpretation(tmp_path):
    host, _, bridge, _ = setup(tmp_path)
    artifact = host.artifacts / host.receipts["b"]["artifact_sha"]
    artifact.write_bytes(b"different bytes")
    with pytest.raises(IntakeError, match="digest mismatch"):
        bridge.inspect("b", "attempt-b")
