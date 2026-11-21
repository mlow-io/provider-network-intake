"""A compact catalog of declared source contracts and canonical field meanings."""

from __future__ import annotations

from typing import Any

from .errors import IntakeError

FIELDS = frozenset(
    {
        "record_id",
        "record_action",
        "practitioner_npi",
        "billing_npi",
        "group_npi",
        "entity_class",
        "provider_name",
        "location_id",
        "address",
        "address_line2",
        "city",
        "state",
        "zip",
        "phone",
        "phone_extension",
        "fax",
        "directory_visible",
        "panel_status",
        "payer_id",
        "network_id",
        "plan_id",
        "reported_status",
        "effective_start",
        "effective_end",
        "office_end",
        "credentialing_end",
        "source_updated_at",
        "specialty",
    }
)
BOOLEAN_FIELDS = frozenset({"directory_visible"})
DATE_FIELDS = frozenset(
    {"effective_start", "effective_end", "office_end", "credentialing_end", "source_updated_at"}
)
IDENTIFIER_FIELDS = frozenset({"practitioner_npi", "billing_npi", "group_npi"})
CONTRACTS: dict[str, dict[str, Any]] = {
    "payer-network-roster/1": {
        "format": "csv",
        "grain": "source_declared",
        "required": ["practitioner_npi"],
        "capabilities": ["inspection", "source_assertions", "reviewed_adaptation"],
        "limits": {"bytes": 10485760, "rows": 10000, "columns": 64},
    },
    "clinic-roster/1": {
        "format": "csv",
        "grain": "practitioner_office",
        "required": ["practitioner_npi", "location_id"],
        "capabilities": ["inspection", "office_facts", "reviewed_adaptation"],
        "limits": {"bytes": 10485760, "rows": 10000, "columns": 64},
    },
    "nppes-api-2.1/1": {
        "format": "nppes",
        "grain": "provider_with_addresses",
        "required": [],
        "capabilities": ["registry_reference"],
        "network_participation": "not_asserted",
    },
    "plannet-r4-1.2-subset/1": {
        "format": "plannet",
        "grain": "native_resource_graph",
        "required": [],
        "fhir_version": "4.0.1",
        "guide_version": "1.2.0",
        "supported_resources": ["Practitioner", "Organization", "Location", "PractitionerRole"],
        "profile_validation": "not_run",
        "terminology_validation": "not_run",
    },
}


def contract(name: str) -> dict[str, Any]:
    if not isinstance(name, str):
        raise IntakeError("Contract identifier must be text")
    try:
        return CONTRACTS[name]
    except KeyError as exc:
        raise IntakeError(f"Unsupported contract: {name}") from exc
