"""Installed-library integration against Provider Directory Workbench's actual services."""
import json

import pytest
from fastapi.testclient import TestClient

from provider_directory_truth_layer.intake_api import create_workbench
from provider_directory_truth_layer.intake_store import IntakeStore


def test_source_catalog_review_preserves_host_records_and_reuses_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("PDTL_SCHEMA_CATALOG_ENABLED", "1")
    database, retained = tmp_path / "directory.sqlite3", tmp_path / "retained"
    app = create_workbench(database, retained, demo=False)
    host = IntakeStore(database, retained)
    original = b"NPI,Site,Directory Flag\n1000000004,NORTH,Y\n1000000004,SOUTH,N\n"
    receipt = host.receive(original, "synthetic-roster.csv", source="synthetic-catalog-sender")
    with host.db() as conn:
        before = list(conn.iterdump())
    client = TestClient(app)
    base = f"/api/intakes/{receipt['id']}/source-schema"
    context = {"source_version": "synthetic-guide-v2"}
    common = {"expected_attempt": receipt["attempt_id"], "context": context}
    inspected = client.post(base + "/inspect", json=common)
    assert inspected.status_code == 200, inspected.text
    data = inspected.json()
    assert data["recognition"]["status"] == "unknown"
    definition = data["draft"]
    definition.update(grain="practitioner_office", mapping={"practitioner_npi": "NPI", "location_id": "Site", "directory_visible": "Directory Flag"},
                      transforms={"directory_visible": [{"op": "value_map", "values": {"Y": False, "N": True}}]},
                      evidence_refs=["synthetic-note"], source_notes={"synthetic-note": "NPI is practitioner, Site office key; Y excludes and N displays. Original synthetic instructions."})
    preview = client.post(base + "/preview", json={**common, "definition": definition, "expected_catalog_revision": 0})
    assert preview.status_code == 200, preview.text
    result = preview.json()
    assert result["report"]["rows"][0]["normalized"]["directory_visible"] is False
    assert result["report"]["rows"][1]["normalized"]["directory_visible"] is True
    assert result["report"]["eligibility"]["state"] == "held"
    proposed = client.post(base + "/proposals", json={**common, "definition": definition, "expected_catalog_revision": 0, "preview_token": result["preview_token"], "request_id": "source-review-b"})
    assert proposed.status_code == 200, proposed.text
    reviewed = client.post(base + f"/proposals/{proposed.json()['id']}/review", json={"expected_attempt": receipt["attempt_id"], "actor": "Synthetic reviewer", "reason": "Checked source instructions and both office flags", "expected_catalog_revision": 0, "preview_token": result["preview_token"]})
    assert reviewed.status_code == 200, reviewed.text
    with host.db() as conn:
        assert list(conn.iterdump()) == before
    assert (host.artifacts / receipt["artifact_sha"]).read_bytes() == original
    next_receipt = host.receive(original, "synthetic-roster.csv", source="synthetic-catalog-sender")
    reopened = TestClient(create_workbench(database, retained, demo=False))
    matched = reopened.post(f"/api/intakes/{next_receipt['id']}/source-schema/inspect", json={"expected_attempt": next_receipt["attempt_id"], "context": context})
    assert matched.status_code == 200
    assert matched.json()["recognition"]["status"] == "exact"
    assert matched.json()["draft"]["source_notes"]["synthetic-note"].startswith("NPI is practitioner")


def test_source_catalog_rejects_stale_host_attempt_before_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("PDTL_SCHEMA_CATALOG_ENABLED", "1")
    database, retained = tmp_path / "directory.sqlite3", tmp_path / "retained"
    app = create_workbench(database, retained, demo=False)
    host = IntakeStore(database, retained)
    receipt = host.receive(b"Practitioner NPI,Office ID\n1000000004,NORTH\n", "synthetic.csv", source="synthetic")
    client = TestClient(app)
    response = client.post(f"/api/intakes/{receipt['id']}/source-schema/inspect", json={"expected_attempt": "stale-attempt"})
    assert response.status_code == 409
    assert app.state.source_catalog.catalog.history() == []


def test_plugin_mount_is_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("PDTL_SCHEMA_CATALOG_ENABLED", raising=False)
    app = create_workbench(tmp_path / "directory.sqlite3", tmp_path / "retained", demo=False)
    assert all("source-schema" not in route.path for route in app.routes)


def test_format_review_cannot_replace_a_real_approved_consumer_export(tmp_path):
    from test_managed_feed import setup_feed, deliver, approve
    from provider_directory_truth_layer.approved_consumer import consume
    from provider_directory_truth_layer.intake_store import packed
    from provider_network_intake import CatalogStore
    from provider_network_intake.workbench import WorkbenchBridge

    host, feeds, feed, inbox = setup_feed(tmp_path)
    deliver(inbox)
    version = feeds.run_once(feed['id'], '2026-10-06T10:00:00+00:00')['version_id']
    approve(feeds, version)
    original_export = packed(feeds.export(version)).encode()
    contacts = consume(original_export, version, expected_feed=feed['id'])
    receipt = host.receive(b'NPI,Site,Directory Flag\n1000000004,NORTH,Y\n', 'changed.csv', source='synthetic-changed-format')
    with host.db() as conn:
        before = list(conn.iterdump())
    bridge = WorkbenchBridge(host, CatalogStore(host.root / 'source-catalog.sqlite3'))
    draft = bridge.inspect(receipt['id'], receipt['attempt_id'])['draft']
    draft.update(grain='practitioner_office', mapping={'practitioner_npi':'NPI','location_id':'Site','directory_visible':'Directory Flag'},
                 transforms={'directory_visible':[{'op':'value_map','values':{'Y':False}}]},
                 evidence_refs=['synthetic-guide'], source_notes={'synthetic-guide':'Original synthetic guide: Y excludes this office; no participation status is supplied.'})
    preview = bridge.preview(receipt['id'], receipt['attempt_id'], draft, 0)
    assert preview['report']['eligibility']['state'] == 'held'
    proposal = bridge.propose(receipt['id'], receipt['attempt_id'], draft, 0, preview['preview_token'], 'format-review')
    bridge.review(receipt['id'], receipt['attempt_id'], proposal['id'], actor='Synthetic reviewer', reason='Checked guide', expected_catalog_revision=0, preview_token=preview['preview_token'])
    with host.db() as conn:
        assert list(conn.iterdump()) == before
    assert feeds.current(feed['id'])['version_id'] == version
    assert packed(feeds.export(version)).encode() == original_export
    assert consume(original_export, version, expected_feed=feed['id']) == contacts
