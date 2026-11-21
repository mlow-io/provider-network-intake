"""Immutable public descriptors. Source facts, schema recognition and eligibility stay distinct."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .errors import IntakeError
from .jsonio import digest, object_keys, text

Outcome = Literal["passed", "failed", "incomplete", "not_run", "unavailable", "not_applicable"]


@dataclass(frozen=True, slots=True)
class SourceContext:
    source: str
    delivery_id: str
    payer: str | None = None
    network: str | None = None
    completeness: Literal["unknown", "partial", "full", "delta"] = "unknown"
    source_version: str | None = None

    def __post_init__(self) -> None:
        text(self.source, "source")
        text(self.delivery_id, "delivery_id")
        if not isinstance(self.completeness, str) or self.completeness not in {
            "unknown",
            "partial",
            "full",
            "delta",
        }:
            raise IntakeError("Invalid delivery completeness")
        for name in ("payer", "network", "source_version"):
            value = getattr(self, name)
            if value is not None:
                text(value, name)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SourceContext:
        return cls(
            **object_keys(
                value,
                {"source", "delivery_id", "payer", "network", "completeness", "source_version"},
                {"source", "delivery_id"},
            )
        )

    @property
    def scope(self) -> tuple[str, str | None, str | None, str | None]:
        """A delivery's identity is distinct from a reusable source/payer/network scope."""
        return self.source, self.payer, self.network, self.source_version


def definition_scope(definition: dict[str, Any]) -> tuple[str, str | None, str | None, str | None]:
    return (
        definition["source"],
        definition.get("payer"),
        definition.get("network"),
        definition.get("source_version"),
    )


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    layer: str
    locator: str
    message: str
    severity: Literal["error", "warning", "info"] = "error"
    blocks: tuple[str, ...] = ("participation_export",)
    rule_version: int = 1


@dataclass(slots=True)
class Findings:
    """All counts/blockers are assessed even when detailed findings are bounded."""

    limit: int = 200
    items: list[Finding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)
    failed_layers: set[str] = field(default_factory=set)
    incomplete_layers: set[str] = field(default_factory=set)

    def add(self, finding: Finding) -> None:
        self.counts[finding.code] = self.counts.get(finding.code, 0) + 1
        self.blocked.update(finding.blocks)
        if finding.severity == "error":
            self.failed_layers.add(finding.layer)
        elif finding.blocks:
            self.incomplete_layers.add(finding.layer)
        if len(self.items) < self.limit:
            self.items.append(finding)

    def outcome(self, layer: str) -> Outcome:
        if layer in self.failed_layers:
            return "failed"
        return "incomplete" if layer in self.incomplete_layers else "passed"

    def to_dict(self) -> dict[str, Any]:
        count = sum(self.counts.values())
        return {
            "items": [{**asdict(item), "blocks": list(item.blocks)} for item in self.items],
            "counts": self.counts.copy(),
            "total": count,
            "truncated": count > len(self.items),
            "blocked": sorted(self.blocked),
        }


@dataclass(frozen=True, slots=True)
class Inspection:
    context: SourceContext
    artifact_sha256: str
    format: str
    fields: tuple[str, ...]
    row_count: int
    descriptor: dict[str, Any]
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        # Ordering does not change meaning; original field names do.
        return digest(
            {"format": self.format, "fields": sorted(self.fields), "settings": self.settings}
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "fields": list(self.fields), "signature": self.signature}
