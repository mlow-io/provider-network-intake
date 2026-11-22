"""Shared interpretation boundary for Python, CLI, agent tools and host applications."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import asdict
from typing import Any

from .adapters import csv_roster, native
from .catalog_store import CatalogSnapshot
from .contracts import CONTRACTS
from .errors import Conflict, IntakeError
from .jsonio import bytes_digest, digest, object_keys
from .models import Finding, Findings, Inspection, SourceContext, definition_scope
from .recipes import PreparedRecipe, Variant, WorkBudget, detail_cost
from .recognition import Recognition, get_variant, normalized_header, recognize

ALIASES = {
    "practitioner_npi": {"practitioner npi", "rendering npi", "individual npi"},
    "billing_npi": {"billing npi"},
    "group_npi": {"group npi", "organization npi"},
    "location_id": {"office id", "location id", "site id"},
    "provider_name": {"provider name", "practitioner name"},
    "phone": {"office phone", "practice phone"},
    "fax": {"office fax", "fax"},
    "zip": {"zip", "zip code", "postal code"},
    "state": {"state"},
    "city": {"city"},
    "address": {"office address", "street address"},
    "record_id": {"record id", "membership id"},
    "entity_class": {"entity class", "classification"},
}


class Engine:
    """Stateless interpreter. Caller-owned catalog snapshots make execution reproducible."""

    @classmethod
    def builtin(cls) -> Engine:
        return cls()

    def inspect(
        self,
        payload: bytes,
        *,
        context: SourceContext,
        format_hint: str = "csv",
        settings: dict[str, Any] | None = None,
    ) -> Inspection:
        if not isinstance(format_hint, str):
            raise IntakeError("Format hint must be text")
        if format_hint == "csv":
            options = object_keys(
                {} if settings is None else settings, {"delimiter", "encoding"}, set()
            )
            return csv_roster.inspect(payload, context, **options)
        if format_hint in {"nppes", "plannet"}:
            if settings:
                raise IntakeError("Native sources do not accept CSV settings")
            return native.inspect(payload, context, format_hint)
        raise IntakeError(f"Unsupported format: {format_hint}")

    def preview_native(
        self,
        payload: bytes,
        *,
        context: SourceContext,
        format_hint: str,
        detail_limit: int = 100,
        finding_limit: int = 200,
    ) -> dict[str, Any]:
        if not isinstance(format_hint, str) or format_hint not in {"nppes", "plannet"}:
            raise IntakeError("Native preview requires nppes or plannet")
        if (
            type(detail_limit) is not int
            or not 0 <= detail_limit <= 1000
            or type(finding_limit) is not int
            or not 0 <= finding_limit <= 1000
        ):
            raise IntakeError("Invalid native detail/finding limit")
        inspection = self.inspect(payload, context=context, format_hint=format_hint)
        return native.preview(
            payload, inspection, detail_limit=detail_limit, finding_limit=finding_limit
        )

    def recognize(self, inspection: Inspection, *, catalog: CatalogSnapshot) -> Recognition:
        return recognize(inspection, catalog)

    def propose_adaptation(
        self,
        inspection: Inspection,
        *,
        recognition: Recognition | None = None,
        catalog: CatalogSnapshot | None = None,
    ) -> dict[str, Any]:
        if recognition and recognition.selected_id:
            if catalog is None or recognition.catalog_revision != catalog.revision:
                raise Conflict("Selected recognition requires its catalog snapshot")
            variant = get_variant(catalog, recognition.selected_id)
            definition = variant.to_dict()
            definition["headers"] = list(inspection.fields)
            return self.proposal(
                inspection,
                Variant.from_dict(definition),
                catalog_revision=catalog.revision,
                kind="existing_match",
            )
        mapping: dict[str, str] = {}
        for field, aliases in ALIASES.items():
            columns = [h for h in inspection.fields if normalized_header(h) in aliases]
            if len(columns) == 1:
                mapping[field] = columns[0]
        definition = {
            "version": 1,
            "source": inspection.context.source,
            "payer": inspection.context.payer,
            "network": inspection.context.network,
            "source_version": inspection.context.source_version,
            "contract": "payer-network-roster/1",
            "grain": "source_declared",
            "headers": list(inspection.fields),
            "mapping": mapping,
            "settings": inspection.settings,
        }
        return self.proposal(
            inspection,
            Variant.from_dict(definition),
            catalog_revision=recognition.catalog_revision if recognition else 0,
            kind="new_variant",
        )

    def proposal(
        self,
        inspection: Inspection,
        variant: Variant,
        *,
        catalog_revision: int,
        kind: str = "new_variant",
        evidence_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        if type(catalog_revision) is not int or catalog_revision < 0:
            raise IntakeError("Catalog revision must be a nonnegative integer")
        if kind not in {
            "new_variant",
            "existing_match",
            "mapping_correction",
            "source_contract_correction",
            "validator_defect",
        }:
            raise IntakeError("Unsupported adaptation proposal kind")
        variant = Variant.from_dict(variant.to_dict())
        definition = variant.to_dict()
        if definition_scope(definition) != inspection.context.scope:
            raise IntakeError("Proposal crosses source/payer/network scope")
        if (
            set(definition["headers"]) != set(inspection.fields)
            or definition["settings"] != inspection.settings
        ):
            raise IntakeError("Proposal does not describe current input fields/settings")
        body = {
            "version": 1,
            "kind": kind,
            "artifact_sha256": inspection.artifact_sha256,
            "input_signature": inspection.signature,
            "context": asdict(inspection.context),
            "catalog_revision": catalog_revision,
            "variant": definition,
            "evidence_refs": evidence_refs or definition["evidence_refs"],
        }
        return {"id": digest(body), **body}

    def preview(
        self,
        payload: bytes,
        *,
        proposal: dict[str, Any],
        context: SourceContext,
        expected_catalog_revision: int | None = None,
        detail_limit: int = 100,
        finding_limit: int = 200,
    ) -> dict[str, Any]:
        if (
            type(detail_limit) is not int
            or not 0 <= detail_limit <= 10000
            or type(finding_limit) is not int
            or not 0 <= finding_limit <= 1000
        ):
            raise IntakeError("Preview detail/finding limits must be bounded integers")
        if not isinstance(proposal, dict) or "variant" not in proposal or "id" not in proposal:
            raise IntakeError("Expected an adaptation proposal")
        allowed = {
            "id",
            "version",
            "kind",
            "artifact_sha256",
            "input_signature",
            "context",
            "catalog_revision",
            "variant",
            "evidence_refs",
            "inspection",
            "origin",
            "request_id",
        }
        if (
            set(proposal) - allowed
            or type(proposal.get("version")) is not int
            or proposal["version"] != 1
        ):
            raise IntakeError("Unsupported proposal keys/version")
        if type(proposal.get("catalog_revision")) is not int or proposal["catalog_revision"] < 0:
            raise IntakeError("Invalid proposal catalog revision")
        body = {k: v for k, v in proposal.items() if k != "id"}
        if (
            proposal["id"] != digest(body)
            or proposal.get("artifact_sha256") != bytes_digest(payload)
            or proposal.get("context") != asdict(context)
        ):
            raise Conflict("Proposal content, artifact or delivery context changed")
        if (
            expected_catalog_revision is not None
            and proposal["catalog_revision"] != expected_catalog_revision
        ):
            raise Conflict("Catalog revision changed; prepare a new proposal")
        variant = Variant.from_dict(proposal["variant"])
        recipe = PreparedRecipe.compile(variant)
        definition = recipe.definition
        source_rows = csv_roster.rows(payload, **definition["settings"])
        _, header_row = next(source_rows)
        inspection = Inspection(
            context, bytes_digest(payload), "csv", tuple(header_row), 0, {}, definition["settings"]
        )
        if inspection.signature != proposal["input_signature"]:
            raise Conflict("Input schema/settings changed")
        # Verify scope again even if a caller supplied a manually constructed proposal.
        self.proposal(inspection, variant, catalog_revision=proposal["catalog_revision"])
        findings = Findings(limit=finding_limit)
        unmapped = sorted(
            set(inspection.fields)
            - set(definition["mapping"].values())
            - set(definition["ignored_fields"])
        )
        for column in unmapped:
            findings.add(
                Finding(
                    "uninterpreted_field",
                    "structure",
                    "$",
                    f"Source field has no reviewed interpretation or explicit exclusion: {column}",
                    "warning",
                )
            )
        result_rows: list[dict[str, Any]] = []
        retained_cost = 0
        work = WorkBudget()
        total = 0
        for index, original in source_rows:
            total += 1
            locator = f"$.records[{index - 1}]"
            row = recipe.transform(original, locator, findings, work)
            values = row["normalized"]
            if not context.network and not values.get("network_id"):
                findings.add(
                    Finding(
                        "network_scope_unresolved",
                        "relationships",
                        locator,
                        "No declared network context; directory visibility is not participation",
                        "warning",
                    )
                )
            if definition["grain"] == "source_declared":
                findings.add(
                    Finding(
                        "grain_unresolved",
                        "relationships",
                        locator,
                        "Source entity/relationship grain requires review",
                        "warning",
                    )
                )
            if definition["grain"] == "practitioner_office" and not values.get("location_id"):
                findings.add(
                    Finding(
                        "location_scope_unresolved",
                        "relationships",
                        locator,
                        "Office-grain interpretation requires an explicit source office key",
                    )
                )
            if not values.get("reported_status"):
                findings.add(
                    Finding(
                        "participation_status_unresolved",
                        "policy",
                        locator,
                        "No explicit source-reported participation status",
                        "warning",
                    )
                )
            if len(result_rows) < detail_limit:
                cost = detail_cost(row)
                if retained_cost + cost <= 4 * 1024 * 1024:
                    result_rows.append(row)
                    retained_cost += cost
        if total == 0:
            findings.add(
                Finding(
                    "empty_delivery",
                    "policy",
                    "$",
                    "An empty delivery is not an approved replacement dataset",
                )
            )
        layers = {
            name: findings.outcome(name)
            for name in ("decode", "structure", "source_contract", "relationships")
        }
        layers.update(
            profile="not_applicable",
            terminology="not_run",
            policy="incomplete" if findings.blocked else "passed",
        )
        report = {
            "format_version": 1,
            "producer": "provider-network-intake/0.1.0",
            "artifact_sha256": inspection.artifact_sha256,
            "context": asdict(context),
            "source_signature": inspection.signature,
            "variant_id": variant.id,
            "variant": definition,
            "catalog_revision": proposal["catalog_revision"],
            "layers": layers,
            "coverage": "partial" if unmapped else "complete_for_declared_scope",
            "uninterpreted_fields": unmapped,
            "declared_completeness": context.completeness,
            "rows": result_rows,
            "row_count": total,
            "rows_truncated": len(result_rows) < total,
            "retained_detail_budget": 4 * 1024 * 1024,
            "findings": findings.to_dict(),
            "approval": "not_performed",
            "notice": "Source-reported evidence only; NPI registry existence, payer submission completeness and real-world participation are not verified.",
        }
        report["eligibility"] = self.assess(report)
        return report

    def assess(
        self, report: dict[str, Any], *, policy: str = "participation_export/1"
    ) -> dict[str, Any]:
        if policy not in {"participation_export/1", "reference_inspection/1"}:
            raise IntakeError("Unknown consumer policy")
        blocked = report["findings"]["blocked"]
        reasons = list(report["findings"]["counts"]) if "participation_export" in blocked else []
        if policy == "participation_export/1":
            # Catalog review and host acceptance are independent from row eligibility.
            reasons += ["dataset_acceptance_required"]
            state = "held" if blocked else "eligible_for_host_review"
        else:
            state = "inspectable"
        return {
            "policy": policy,
            "state": state,
            "reasons": sorted(set(reasons)),
            "approved": False,
        }

    def iter_evidence(
        self,
        payload: bytes,
        *,
        proposal: dict[str, Any],
        context: SourceContext,
        expected_catalog_revision: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield full tabular lineage after complete bounded validation; does not accept data.

        Assessment and detail use separate passes so callers can retain the complete
        findings summary while streaming details. Source bytes are immutable input.
        """
        self.preview(
            payload,
            proposal=proposal,
            context=context,
            expected_catalog_revision=expected_catalog_revision,
            detail_limit=0,
            finding_limit=0,
        )
        recipe = PreparedRecipe.compile(Variant.from_dict(proposal["variant"]))
        work = WorkBudget()
        for index, original in csv_roster.rows(payload, **recipe.definition["settings"]):
            if index:
                yield recipe.transform(original, f"$.records[{index - 1}]", Findings(limit=0), work)

    def contracts(self) -> dict[str, Any]:
        # Return independent JSON-compatible definitions rather than mutable module state.
        return json.loads(json.dumps(CONTRACTS))  # type: ignore[no-any-return]
