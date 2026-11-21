from __future__ import annotations

import pytest

from provider_network_intake import Engine, IntakeError, ResourceLimit, SourceContext, Variant
from provider_network_intake.models import Findings
from provider_network_intake.recipes import PreparedRecipe, WorkBudget, detail_cost


def test_wide_invalid_json_does_not_allocate_a_locator_for_every_rejected_node():
    import tracemalloc

    from provider_network_intake.jsonio import walk

    value = ["synthetic"] * 100_000
    tracemalloc.start()
    try:
        with pytest.raises(ResourceLimit):
            list(walk(value, max_nodes=100))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


def definition(headers, mapping, **extra):
    return Variant.from_dict(
        {
            "version": 1,
            "source": "synthetic",
            "contract": "payer-network-roster/1",
            "grain": "practitioner_office",
            "headers": headers,
            "mapping": mapping,
            **extra,
        }
    )


def test_repeated_transform_work_is_bounded_across_rows():
    variant = definition(
        ["NPI"], {"practitioner_npi": "NPI"}, transforms={"practitioner_npi": ["trim", "trim"]}
    )
    recipe, budget = PreparedRecipe.compile(variant), WorkBudget(limit=39)
    recipe.transform({"NPI": "1000000004"}, "$.records[0]", Findings(), budget)
    with pytest.raises(ResourceLimit):
        recipe.transform({"NPI": "1000000004"}, "$.records[1]", Findings(), budget)


def test_detail_budget_caps_retained_output_without_skipping_assessment():
    headers = ["NPI", "Site", "Description"]
    variant = definition(
        headers,
        {"practitioner_npi": "NPI", "location_id": "Site", "provider_name": "Description"},
        transforms={"provider_name": ["uppercase"] * 16},
    )
    payload = ("NPI,Site,Description\n" + ("1000000004,NORTH," + "x" * 8192 + "\n") * 30).encode()
    engine, context = Engine(), SourceContext("synthetic", "large")
    proposal = engine.proposal(
        engine.inspect(payload, context=context), variant, catalog_revision=0
    )
    report = engine.preview(payload, proposal=proposal, context=context, detail_limit=1000)
    assert report["row_count"] == 30 and report["rows_truncated"]
    assert 0 < len(report["rows"]) < 30
    assert sum(map(detail_cost, report["rows"])) <= report["retained_detail_budget"]
    assert report["findings"]["counts"]["participation_status_unresolved"] == 30


def test_blank_classifier_is_unassessed_not_optional():
    variant = definition(
        ["NPI", "Class"],
        {"practitioner_npi": "NPI", "entity_class": "Class"},
        required_when=[{"field": "entity_class", "equals": "GROUP", "require": ["group_npi"]}],
    )
    row = PreparedRecipe.compile(variant)
    findings = Findings()
    row.transform({"NPI": "1000000004", "Class": ""}, "$.records[0]", findings)
    assert findings.counts["condition_unassessed"] == 1
    assert findings.blocked


@pytest.mark.parametrize("bad", [[], {}, True, None])
def test_malformed_settings_are_typed_errors(bad):
    engine, context = Engine(), SourceContext("synthetic", "bad")
    with pytest.raises(IntakeError):
        engine.inspect(b"NPI\n1000000004\n", context=context, settings={"delimiter": bad})
    with pytest.raises(IntakeError):
        SourceContext.from_dict({"source": "synthetic", "delivery_id": "bad", "completeness": bad})
