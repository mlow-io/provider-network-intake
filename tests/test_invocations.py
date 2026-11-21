from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from provider_network_intake import CatalogSnapshot, Conflict, Engine, SourceContext
from provider_network_intake.assistance import build_request
from provider_network_intake.invocations import InvocationJournal, run_assistance


def inputs():
    inspection = Engine().inspect(
        b"Practitioner NPI\n1000000004\n", context=SourceContext("synthetic", "one")
    )
    catalog = CatalogSnapshot(0, ())
    return inspection, catalog, {"note": "Original synthetic source instruction."}


def test_completed_invocation_is_replayed_locally_without_a_second_call(tmp_path):
    inspection, catalog, evidence = inputs()

    class Provider:
        model = "controlled-test-provider"
        calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            return {
                "state": "abstained",
                "proposal": None,
                "explanation": "Insufficient source context",
            }

    provider = Provider()
    journal = InvocationJournal(tmp_path / "calls.sqlite3")
    args = dict(
        request_id="call-a",
        provider=provider,
        inspection=inspection,
        catalog=catalog,
        evidence=evidence,
        external_processing=True,
    )
    first = run_assistance(journal, **args)
    second = run_assistance(InvocationJournal(tmp_path / "calls.sqlite3"), **args)
    assert first == second and first["state"] == "completed"
    assert provider.calls == 1


def test_running_or_interrupted_marker_never_triggers_automatic_replay(tmp_path):
    inspection, catalog, evidence = inputs()
    journal = InvocationJournal(tmp_path / "calls.sqlite3")
    context = build_request(inspection, catalog, evidence=evidence)
    journal.start("unknown", context, model="test-provider")

    class Provider:
        model = "test-provider"

        def invoke(self, *args, **kwargs):
            pytest.fail("An unknown existing invocation must never repeat the remote call")

    with pytest.raises(Conflict, match="no automatic replay"):
        run_assistance(
            journal,
            request_id="unknown",
            provider=Provider(),
            inspection=inspection,
            catalog=catalog,
            evidence=evidence,
            external_processing=True,
        )
    assert journal.get("unknown")["state"] == "running"


def test_failure_records_unknown_outcome_and_does_not_persist_exception_secrets(tmp_path):
    inspection, catalog, evidence = inputs()
    journal = InvocationJournal(tmp_path / "calls.sqlite3")

    class Provider:
        model = "test-provider"
        calls = 0

        def invoke(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError("sensitive-transport-secret")

    provider = Provider()
    args = dict(
        request_id="failed",
        provider=provider,
        inspection=inspection,
        catalog=catalog,
        evidence=evidence,
        external_processing=True,
    )
    result = run_assistance(journal, **args)
    assert result["state"] == "failed" and result["external_outcome"] == "unknown"
    assert "sensitive-transport-secret" not in str(result)
    assert run_assistance(journal, **args) == result
    assert provider.calls == 1


def test_cancellation_fences_late_proposal_publication(tmp_path):
    inspection, catalog, evidence = inputs()
    journal = InvocationJournal(tmp_path / "calls.sqlite3")

    class Provider:
        model = "test-provider"

        def invoke(self, *args, **kwargs):
            journal.cancel("cancel", expected_revision=1, reason="Analyst cancelled")
            return {"state": "proposed", "proposal": {"test": "late-result"}}

    result = run_assistance(
        journal,
        request_id="cancel",
        provider=Provider(),
        inspection=inspection,
        catalog=catalog,
        evidence=evidence,
        external_processing=True,
    )
    assert result["state"] == "cancelled"
    assert result["result"] is None
    assert result["quarantined_response"]["proposal"] == {"test": "late-result"}


def test_atomic_journal_claim_under_concurrent_clients(tmp_path):
    inspection, catalog, evidence = inputs()
    journal = InvocationJournal(tmp_path / "calls.sqlite3")
    context = build_request(inspection, catalog, evidence=evidence)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: journal.start("shared", context, model="test-provider")[0], range(2))
        )
    assert sorted(results) == [False, True]
    with pytest.raises(Conflict):
        journal.start("shared", context, model="another-model")
