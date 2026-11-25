"""Tests for healthcare operational domain rules promoted into provider-network-intake."""

from __future__ import annotations

from provider_network_intake import (
    CatalogSnapshot,
    Engine,
    SourceContext,
    assess_licensure_state_consistency,
    classify_phone_conflict,
    classify_regulatory_regime,
    is_hospital_based_taxonomy,
)
from provider_network_intake.exports import markdown_report


def test_hospital_based_taxonomy_and_regime_classification() -> None:
    assert is_hospital_based_taxonomy("207P00000X Emergency Medicine") is True
    assert is_hospital_based_taxonomy("2085R0202X Diagnostic Radiology") is True
    assert is_hospital_based_taxonomy("207Q00000X Family Medicine") is False

    assert classify_regulatory_regime("Volunteer Commercial PPO")["regime"] == "commercial_nsa"
    assert (
        classify_regulatory_regime("MidSouth Medicare Advantage")["regime"] == "medicare_advantage"
    )
    assert classify_regulatory_regime("TennCare Community Plan")["regime"] == "medicaid_mco"
    assert classify_regulatory_regime("FEDVIP Dental PPO")["regime"] == "delegated_dental_vision"
    assert classify_regulatory_regime(None)["regime"] == "unclassified_regime"


def test_phone_conflict_taxonomy() -> None:
    assert classify_phone_conflict("(615) 555-0101", "1-615-555-0101")["category"] == "exact_match"
    assert (
        classify_phone_conflict("800-555-0199", "615-555-0142")["category"]
        == "toll_free_centralized_line"
    )
    assert (
        classify_phone_conflict("615-555-0101", "615-555-0142")["category"]
        == "same_npa_nxx_clinic_pbx"
    )
    assert (
        classify_phone_conflict("615-322-0101", "615-885-0142")["category"]
        == "same_area_code_different_exchange"
    )
    assert (
        classify_phone_conflict("615-555-0101", "270-555-0142")["category"]
        == "different_area_code_or_state"
    )


def test_licensure_gate_never_promotes_self_reported_to_psv_verified() -> None:
    match = assess_licensure_state_consistency("TN", "TN", psv_verified=False)
    assert match["status"] == "self_reported_state_match_unverified_psv"
    assert match["psv_required"] == "true"

    mismatch = assess_licensure_state_consistency("TN", "KY", psv_verified=False)
    assert mismatch["status"] == "out_of_state_license_only_unverified_psv"
    assert mismatch["psv_required"] == "true"

    verified = assess_licensure_state_consistency("TN", "TN", psv_verified=True)
    assert verified["status"] == "primary_source_verified"
    assert verified["psv_required"] == "false"


def test_engine_preview_emits_advisory_domain_warnings_and_regime() -> None:
    payload = (
        b"Practitioner NPI,Office ID,State,License State,NUCC Taxonomy,Accepting New Patients\n"
        b"1990001012,HEND-LAKE,TN,KY,207P00000X Emergency Medicine,newpt\n"
    )
    engine = Engine()
    context = SourceContext(
        source="synthetic-clinic",
        delivery_id="del-1",
        payer="volunteer-ppo",
        network="Volunteer Commercial PPO",
    )
    catalog = CatalogSnapshot(revision=0, variants=())
    inspection = engine.inspect(payload, context=context)
    recognition = engine.recognize(inspection, catalog=catalog)
    proposal = engine.propose_adaptation(inspection, recognition=recognition, catalog=catalog)
    report = engine.preview(payload, proposal=proposal, context=context)

    assert report["regulatory_regime"]["regime"] == "commercial_nsa"
    counts = report["findings"]["counts"]
    assert counts["hospital_based_accepting_new_patients"] == 1
    assert counts["license_state_mismatch_unverified_psv"] == 1

    md = markdown_report(report, context=context)
    assert "Commercial / NSA ERISA" in md
