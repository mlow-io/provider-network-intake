"""Offline native-source inspection. Native resources never pass through clinic-row rules."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from ..contracts import is_valid_npi
from ..errors import IntakeError, ResourceLimit
from ..jsonio import bytes_digest, loads, walk
from ..models import Finding, Findings, Inspection, SourceContext

NPI_SYSTEM = "http://hl7.org/fhir/sid/us-npi"
NETWORK_PROFILE = "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/plannet-Network"
NETWORK_EXTENSION = (
    "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference"
)
SUPPORTED = {"Practitioner", "Organization", "Location", "PractitionerRole"}


def _array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise IntakeError(f"{name} requires an array of objects")
    return value


def inspect(payload: bytes, context: SourceContext, format_hint: str) -> Inspection:
    data = loads(payload)
    if not isinstance(data, dict):
        raise IntakeError("Native source requires a named object envelope")
    if format_hint == "nppes":
        count = data.get("result_count")
        if type(count) is not int or count < 0:
            raise IntakeError("NPPES requires a nonnegative integer result_count")
        entries = _array(data.get("results", [] if count == 0 else None), "NPPES results")
        if len(entries) != count or any(k in data for k in ("Errors", "errors", "error")):
            raise IntakeError("NPPES error/count mismatch")
    elif format_hint == "plannet":
        if data.get("resourceType") != "Bundle" or data.get("type") != "searchset":
            raise IntakeError("Plan-Net subset requires a FHIR R4 searchset Bundle")
        entries = _array(data.get("entry", []), "Bundle.entry")
    else:
        raise IntakeError("Unknown native format")
    if len(entries) > 1000:
        raise ResourceLimit("Native import exceeds 1000 entries")
    fields = tuple(sorted({str(k) for item in entries for k in item}))
    descriptor = {
        "envelope": "results" if format_hint == "nppes" else "Bundle.entry",
        "fields": list(fields),
        "sample_policy": "shape_only",
        "coverage": "declared_native_subset",
    }
    return Inspection(context, bytes_digest(payload), format_hint, fields, len(entries), descriptor)


def preview(
    payload: bytes, inspection: Inspection, *, detail_limit: int = 100, finding_limit: int = 200
) -> dict[str, Any]:
    data = loads(payload)
    findings = Findings(limit=finding_limit)
    rows: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    assertions: list[dict[str, Any]] = []
    if inspection.format == "nppes":
        for index, original in enumerate(data.get("results", [])):
            locator = f"$.results[{index}]"
            basic = original.get("basic", {})
            if not isinstance(basic, dict):
                findings.add(
                    Finding("invalid_basic", "structure", locator, "NPPES basic must be an object")
                )
                basic = {}
            npi = original.get("number")
            if type(npi) is int:
                npi = str(npi)
            if not isinstance(npi, str) or not re.fullmatch(r"[0-9]{10}", npi):
                findings.add(
                    Finding(
                        "invalid_npi_shape",
                        "structure",
                        locator,
                        "Provider NPI requires ten digits; registry existence not assessed",
                    )
                )
            elif not is_valid_npi(npi):
                findings.add(
                    Finding(
                        "invalid_npi_checksum",
                        "structure",
                        locator,
                        "Provider NPI fails CMS Luhn check-digit validation; registry existence not assessed",
                    )
                )
            locations = []
            for array in ("addresses", "practiceLocations"):
                try:
                    addresses = _array(original.get(array, []), array)
                except IntakeError as exc:
                    findings.add(Finding("invalid_addresses", "structure", locator, str(exc)))
                    continue
                for i, address in enumerate(addresses):
                    locations.append(
                        {
                            "locator": f"{locator}.{array}[{i}]",
                            "original": address,
                            "purpose": address.get("address_purpose"),
                            "source_location_id": None,
                        }
                    )
            row = {
                "locator": locator,
                "original": original,
                "normalized": {
                    "npi": npi,
                    "entity_type": original.get("enumeration_type"),
                    "locations": locations,
                    "source_updated_at": basic.get("last_updated"),
                    "network_participation": "not_asserted",
                },
            }
            if len(rows) < detail_limit:
                rows.append(row)
        findings.add(
            Finding(
                "registry_not_participation",
                "policy",
                "$",
                "NPPES evidence does not assert payer/network participation",
                "info",
            )
        )
        count = len(data.get("results", []))
    else:
        rows, references, assertions, count = _fhir(data, findings, detail_limit)
    layers = {name: findings.outcome(name) for name in ("decode", "structure", "relationships")}
    layers.update(
        source_contract="incomplete",
        profile="not_run" if inspection.format == "plannet" else "not_applicable",
        terminology="not_run",
        policy="incomplete",
    )
    return {
        "format_version": 1,
        "producer": "provider-network-intake/0.1.0",
        "artifact_sha256": inspection.artifact_sha256,
        "context": asdict(inspection.context),
        "source_signature": inspection.signature,
        "source_format": inspection.format,
        "contract": "nppes-api-2.1/1"
        if inspection.format == "nppes"
        else "plannet-r4-1.2-subset/1",
        "layers": layers,
        "coverage": "partial",
        "declared_completeness": inspection.context.completeness,
        "rows": rows,
        "row_count": count,
        "rows_truncated": len(rows) < count,
        "references": references,
        "assertions": assertions,
        "findings": findings.to_dict(),
        "approval": "not_performed",
        "eligibility": {
            "policy": "participation_export/1",
            "state": "held",
            "reasons": ["native_reference_only", "profile_not_run", "dataset_acceptance_required"],
            "approved": False,
        },
        "notice": "Bounded source-native reference evidence; profile, terminology, cross-source identities and real-world participation are unverified.",
    }


def _fhir(
    data: dict[str, Any], findings: Findings, detail_limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int]:
    resources: list[dict[str, Any]] = []
    index: dict[str, list[dict[str, Any]]] = {}
    for i, entry in enumerate(data.get("entry", [])):
        locator = f"$.entry[{i}].resource"
        original = entry.get("resource")
        if not isinstance(original, dict) or not isinstance(original.get("resourceType"), str):
            findings.add(
                Finding(
                    "malformed_resource",
                    "structure",
                    locator,
                    "Entry requires resourceType and resource object",
                )
            )
            resources.append(
                {"locator": locator, "original": original, "eligible_projection": False}
            )
            continue
        kind = original["resourceType"]
        eligible = kind in SUPPORTED
        if not eligible:
            findings.add(
                Finding(
                    "unsupported_resource",
                    "structure",
                    locator,
                    f"{kind} is retained without interpretation",
                    "warning",
                )
            )
        for path, node in walk(original, locator):
            if isinstance(node, dict) and ("modifierExtension" in node or "implicitRules" in node):
                eligible = False
                findings.add(
                    Finding(
                        "unsupported_modifier",
                        "structure",
                        path,
                        "Modifier semantics are not interpreted",
                    )
                )
        row = {
            "locator": locator,
            "resource_type": kind,
            "id": original.get("id"),
            "full_url": entry.get("fullUrl"),
            "original": original,
            "eligible_projection": eligible,
        }
        resources.append(row)
        keys = []
        if isinstance(entry.get("fullUrl"), str):
            keys.append(entry["fullUrl"])
        if isinstance(original.get("id"), str):
            keys.append(f"{kind}/{original['id']}")
        for key in set(keys):
            index.setdefault(key, []).append(row)
    for key, matches in index.items():
        if len(matches) > 1:
            findings.add(
                Finding(
                    "ambiguous_resource_identity",
                    "relationships",
                    "$",
                    f"More than one resource resolves {key}",
                )
            )
            for row in matches:
                row["eligible_projection"] = False
    references: list[dict[str, Any]] = []
    resolved: dict[str, dict[str, Any]] = {}
    for row in resources:
        if not isinstance(row["original"], dict):
            continue
        for path, node in walk(row["original"], row["locator"]):
            if not isinstance(node, dict):
                continue
            if "extension" in node and isinstance(node["extension"], list):
                for extension in node["extension"]:
                    if not isinstance(extension, dict) or extension.get("url") != NETWORK_EXTENSION:
                        findings.add(
                            Finding(
                                "unsupported_extension",
                                "structure",
                                path,
                                "Extension retained without interpretation",
                                "warning",
                            )
                        )
            if "reference" not in node:
                continue
            target = node["reference"]
            matches = index.get(target, []) if isinstance(target, str) else []
            # Relative references resolve within the origin REST base when one is available.
            own = row.get("full_url")
            own_suffix = f"{row.get('resource_type')}/{row.get('id')}"
            if (
                isinstance(target, str)
                and re.fullmatch(r"[A-Za-z]+/[A-Za-z0-9.-]{1,64}", target)
                and isinstance(own, str)
                and own.startswith(("https://", "http://"))
                and own.endswith(own_suffix)
            ):
                matches = index.get(own[: -len(own_suffix)] + target, [])
            state = "resolved" if len(matches) == 1 else "ambiguous" if matches else "unresolved"
            reference = {
                "origin": row["locator"],
                "path": path + '["reference"]',
                "raw_target": target,
                "state": state,
                "target": matches[0]["locator"] if state == "resolved" else None,
            }
            references.append(reference)
            if state == "resolved":
                resolved[reference["path"]] = matches[0]
            else:
                findings.add(
                    Finding(
                        "reference_" + state,
                        "relationships",
                        reference["path"],
                        "No unique within-Bundle reference target",
                    )
                )
    assertions: list[dict[str, Any]] = []
    if "modifierExtension" in data or "implicitRules" in data:
        findings.add(
            Finding(
                "unsupported_bundle_modifier",
                "structure",
                "$",
                "Bundle modifiers prevent automatic projection",
            )
        )
        return resources[:detail_limit], references, assertions, len(resources)
    for role in resources:
        value = role["original"]
        if role.get("resource_type") != "PractitionerRole" or not role["eligible_projection"]:
            continue
        locations = value.get("location", [])
        extensions = value.get("extension", [])
        if (
            not isinstance(locations, list)
            or len(locations) != 1
            or not isinstance(extensions, list)
        ):
            findings.add(
                Finding(
                    "ambiguous_location_scope",
                    "relationships",
                    role["locator"],
                    "Projection requires exactly one role location",
                )
            )
            continue
        nets = [
            (i, e)
            for i, e in enumerate(extensions)
            if isinstance(e, dict) and e.get("url") == NETWORK_EXTENSION
        ]
        if len(nets) > 1:
            findings.add(
                Finding(
                    "ambiguous_network_scope",
                    "relationships",
                    role["locator"],
                    "Multiple network references remain native relationships, not an office cross-product",
                )
            )
            continue
        practitioner = resolved.get(role["locator"] + '["practitioner"]["reference"]')
        location = resolved.get(role["locator"] + '["location"][0]["reference"]')
        network = (
            resolved.get(
                role["locator"] + f'["extension"][{nets[0][0]}]["valueReference"]["reference"]'
            )
            if nets
            else None
        )
        if (
            not practitioner
            or not location
            or practitioner.get("resource_type") != "Practitioner"
            or location.get("resource_type") != "Location"
            or not practitioner["eligible_projection"]
            or not location["eligible_projection"]
        ):
            findings.add(
                Finding(
                    "unresolved_role_context",
                    "relationships",
                    role["locator"],
                    "Required practitioner/location context is unresolved or incompatible",
                )
            )
            continue
        if nets:
            metadata = network["original"].get("meta", {}) if network else {}
            profiles = metadata.get("profile", []) if isinstance(metadata, dict) else []
            if (
                not network
                or network.get("resource_type") != "Organization"
                or not network["eligible_projection"]
                or not isinstance(profiles, list)
                or not any(
                    p in {NETWORK_PROFILE, NETWORK_PROFILE + "|1.2.0"}
                    for p in profiles
                    if isinstance(p, str)
                )
            ):
                findings.add(
                    Finding(
                        "unresolved_network_scope",
                        "relationships",
                        role["locator"],
                        "Network requires the supported explicit Organization Network profile",
                    )
                )
                continue
        assertions.append(
            {
                "kind": "source_reported_role",
                "practitioner": practitioner["locator"],
                "location": location["locator"],
                "network": network["locator"] if network else None,
                "role": role["locator"],
                "period": value.get("period"),
                "active": value.get("active"),
                "reviewed_local_identity": None,
                "verified_participation": False,
            }
        )
    return resources[:detail_limit], references, assertions, len(resources)
