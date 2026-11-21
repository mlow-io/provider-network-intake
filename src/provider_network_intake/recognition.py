"""Deterministic candidate retrieval: structural similarity never proves semantic meaning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .catalog_store import CatalogSnapshot
from .errors import IntakeError
from .models import Inspection, definition_scope
from .recipes import Variant


def normalized_header(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.casefold()).split())


@dataclass(frozen=True, slots=True)
class Recognition:
    status: str
    catalog_revision: int
    candidates: tuple[dict[str, Any], ...]
    selected_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "catalog_revision": self.catalog_revision,
            "candidates": list(self.candidates),
            "selected_id": self.selected_id,
            "notice": "Source-schema recognition is separate from validity, approval and real-world participation.",
        }


def recognize(inspection: Inspection, snapshot: CatalogSnapshot, *, limit: int = 5) -> Recognition:
    if type(limit) is not int or not 1 <= limit <= 20 or len(snapshot.variants) > 1000:
        raise IntakeError("Recognition supports 1–20 results and at most 1000 scoped variants")
    observed = set(inspection.fields)
    normalized = {normalized_header(h) for h in observed}
    candidates = []
    for variant in snapshot.variants:
        definition = variant.to_dict()
        scope = definition_scope(definition)
        if scope[:3] != inspection.context.scope[:3]:
            continue
        version_changed = scope[3] != inspection.context.source_version
        if inspection.format != "csv" or definition["settings"] != inspection.settings:
            continue
        fields = set(definition["headers"])
        common = normalized & {normalized_header(h) for h in fields}
        union = normalized | {normalized_header(h) for h in fields}
        score = len(common) / len(union) if union else 0.0
        if not common:
            continue
        missing = fields - observed
        added = observed - fields
        exact = not missing and not added
        compatible = not (set(definition["mapping"].values()) - observed)
        candidates.append(
            {
                "variant_id": variant.id,
                "contract": definition["contract"],
                "match": "source_version_changed"
                if version_changed
                else "exact"
                if exact
                else "compatible_with_drift"
                if compatible
                else "needs_adaptation",
                "rank_score": score,
                "missing_fields": sorted(missing),
                "added_fields": sorted(added),
                "evidence_refs": definition["evidence_refs"],
            }
        )
    candidates.sort(
        key=lambda c: (
            c["match"] != "exact",
            c["match"] != "compatible_with_drift",
            -c["rank_score"],
            c["variant_id"],
        )
    )
    selectable = [c for c in candidates if c["match"] in {"exact", "compatible_with_drift"}]
    if len(selectable) == 1:
        status = selectable[0]["match"]
        selected = selectable[0]["variant_id"]
    elif len(selectable) > 1:
        status, selected = "ambiguous", None
    else:
        status, selected = "unknown", None
    return Recognition(status, snapshot.revision, tuple(candidates[:limit]), selected)


def get_variant(snapshot: CatalogSnapshot, variant_id: str) -> Variant:
    for variant in snapshot.variants:
        if variant.id == variant_id:
            return variant
    raise IntakeError("Variant is absent from the selected catalog snapshot")
