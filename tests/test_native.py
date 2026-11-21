from __future__ import annotations

import copy
import json

import pytest

from provider_network_intake import Engine, IntakeError, SourceContext
from provider_network_intake.adapters.native import NETWORK_EXTENSION, NETWORK_PROFILE


@pytest.fixture
def graph():
    # Original synthetic graph. These are structural fixtures, not certified profile-valid resources.
    resources = [
        {
            "resourceType": "Practitioner",
            "id": "clinician",
            "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": "1000000004"}],
        },
        {"resourceType": "Location", "id": "north", "address": {"postalCode": "02108"}},
        {"resourceType": "Location", "id": "south"},
        {
            "resourceType": "Organization",
            "id": "network-a",
            "meta": {"profile": [NETWORK_PROFILE + "|1.2.0"]},
        },
        {
            "resourceType": "PractitionerRole",
            "id": "role-north",
            "practitioner": {"reference": "Practitioner/clinician"},
            "location": [{"reference": "Location/north"}],
            "extension": [
                {
                    "url": NETWORK_EXTENSION,
                    "valueReference": {"reference": "Organization/network-a"},
                }
            ],
        },
    ]
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "entry": [
            {
                "fullUrl": f"https://synthetic.invalid/fhir/{r['resourceType']}/{r['id']}",
                "resource": r,
            }
            for r in resources
        ],
    }


def preview(value):
    return Engine().preview_native(
        json.dumps(value).encode(),
        context=SourceContext("synthetic-fhir", "one"),
        format_hint="plannet",
    )


def test_native_graph_retains_paths_and_relationship_grain(graph):
    original = copy.deepcopy(graph)
    report = preview(graph)
    assert len(report["assertions"]) == 1
    assertion = report["assertions"][0]
    assert assertion["location"] == "$.entry[1].resource"
    assert assertion["network"] == "$.entry[3].resource"
    assert assertion["reviewed_local_identity"] is None
    assert assertion["verified_participation"] is False
    assert report["layers"]["profile"] == "not_run"
    assert report["layers"]["terminology"] == "not_run"
    assert report["eligibility"]["state"] == "held"
    assert report["rows"][1]["original"]["address"]["postalCode"] == "02108"
    assert graph == original


@pytest.mark.parametrize(
    "change,code",
    [
        ("missing", "reference_unresolved"),
        ("two_locations", "ambiguous_location_scope"),
        ("two_networks", "ambiguous_network_scope"),
        ("unknown_profile", "unresolved_network_scope"),
        ("modifier", "unsupported_modifier"),
    ],
)
def test_native_ambiguity_never_invents_office_memberships(graph, change, code):
    role = graph["entry"][4]["resource"]
    if change == "missing":
        role["location"][0]["reference"] = "Location/missing"
    elif change == "two_locations":
        role["location"].append({"reference": "Location/south"})
    elif change == "two_networks":
        role["extension"].append(copy.deepcopy(role["extension"][0]))
    elif change == "unknown_profile":
        graph["entry"][3]["resource"]["meta"]["profile"] = [NETWORK_PROFILE + "|999"]
    else:
        role["modifierExtension"] = [
            {"url": "https://synthetic.invalid/unknown", "valueBoolean": True}
        ]
    report = preview(graph)
    assert report["assertions"] == []
    assert code in report["findings"]["counts"]


def test_duplicate_full_url_and_unsupported_resource(graph):
    graph["entry"].append(copy.deepcopy(graph["entry"][1]))
    graph["entry"].append({"resource": {"resourceType": "InsurancePlan", "id": "unknown-plan"}})
    report = preview(graph)
    assert report["assertions"] == []
    assert report["findings"]["counts"]["unsupported_resource"] == 1
    assert "ambiguous_resource_identity" in report["findings"]["counts"]


def test_nppes_keeps_every_address_and_never_claims_network_membership():
    data = {
        "result_count": 1,
        "results": [
            {
                "number": "1000000004",
                "enumeration_type": "NPI-1",
                "basic": {"last_updated": "2026-01-01"},
                "addresses": [
                    {"address_purpose": "MAILING", "postal_code": "02108"},
                    {"address_purpose": "LOCATION", "telephone_number": "5550000000"},
                ],
                "practiceLocations": [{"address_1": "Synthetic South Office"}],
            }
        ],
    }
    report = Engine().preview_native(
        json.dumps(data).encode(),
        context=SourceContext("synthetic-registry", "one"),
        format_hint="nppes",
    )
    row = report["rows"][0]["normalized"]
    assert len(row["locations"]) == 3
    assert row["locations"][0]["original"]["postal_code"] == "02108"
    assert all(loc["source_location_id"] is None for loc in row["locations"])
    assert row["network_participation"] == "not_asserted"
    assert row["source_updated_at"] == "2026-01-01"
    assert report["layers"]["profile"] == "not_applicable"


@pytest.mark.parametrize(
    "data",
    [
        {"result_count": True, "results": [{}]},
        {"result_count": 2, "results": [{}]},
        {"result_count": 0, "Errors": []},
        {"results": []},
    ],
)
def test_nppes_error_envelope_or_count_mismatch_is_not_empty_success(data):
    with pytest.raises(IntakeError):
        Engine().inspect(
            json.dumps(data).encode(), context=SourceContext("registry", "one"), format_hint="nppes"
        )


def test_native_shape_can_be_recorded_without_mislabeling_unsupported_content(graph, tmp_path):
    from provider_network_intake import CatalogStore

    engine = Engine()
    inspection = engine.inspect(
        json.dumps(graph).encode(), context=SourceContext("fhir", "one"), format_hint="plannet"
    )
    store = CatalogStore(tmp_path / "catalog.sqlite3")
    first = store.observe(inspection)
    assert store.observe(inspection) == first
    assert store.snapshot().variants == ()
