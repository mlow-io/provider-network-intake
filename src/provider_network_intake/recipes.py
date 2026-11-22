"""Data-only source interpretations. Compilation happens once per preview, never per row."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .contracts import (
    BOOLEAN_FIELDS,
    DATE_FIELDS,
    FIELDS,
    IDENTIFIER_FIELDS,
    contract,
    is_valid_npi,
)
from .errors import IntakeError, ResourceLimit
from .jsonio import canonical, digest, object_keys, text
from .models import Finding, Findings, SourceContext

OPS = {"trim", "collapse_spaces", "uppercase", "lowercase", "phone_us", "zip_us", "state_us"}
DATE_FORMATS = {"%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"}
GRAINS = {"provider", "practitioner_office", "organization", "source_declared"}
STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI".split()
)


def _strings(value: Any, name: str, *, maximum: int = 64) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise IntakeError(f"{name} must be a bounded list")
    result = tuple(text(v, name) for v in value)
    if len(set(result)) != len(result):
        raise IntakeError(f"{name} cannot contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class Variant:
    """Canonical immutable definition; callers receive independent dictionary copies."""

    definition_json: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Variant:
        keys = {
            "version",
            "source",
            "payer",
            "network",
            "source_version",
            "contract",
            "grain",
            "headers",
            "mapping",
            "transforms",
            "required",
            "required_when",
            "evidence_refs",
            "source_notes",
            "settings",
            "ignored_fields",
        }
        data = object_keys(
            value, keys, {"version", "source", "contract", "grain", "headers", "mapping"}
        )
        if type(data["version"]) is not int or data["version"] != 1:
            raise IntakeError("Variant version must be 1")
        context = SourceContext(
            data["source"],
            "definition",
            data.get("payer"),
            data.get("network"),
            source_version=data.get("source_version"),
        )
        target = contract(data["contract"])
        if target["format"] != "csv":
            raise IntakeError("Reviewed tabular variants require a CSV contract")
        if not isinstance(data["grain"], str) or data["grain"] not in GRAINS:
            raise IntakeError("Unsupported entity grain")
        headers = _strings(data["headers"], "headers")
        if not headers:
            raise IntakeError("Variant requires headers")
        mapping = data["mapping"]
        if not isinstance(mapping, dict) or set(mapping) - FIELDS:
            raise IntakeError("Mapping contains unsupported target fields")
        if any(not isinstance(col, str) or col not in headers for col in mapping.values()):
            raise IntakeError("Mapping must bind existing source headers")
        if len(set(mapping.values())) != len(mapping):
            raise IntakeError("A source column cannot bind multiple target fields")
        transforms = data.get("transforms", {})
        if not isinstance(transforms, dict) or set(transforms) - set(mapping):
            raise IntakeError("Transforms must reference mapped target fields")
        evidence = _strings(data.get("evidence_refs", []), "evidence_refs", maximum=20)
        notes = data.get("source_notes", {})
        if not isinstance(notes, dict) or len(notes) > 10 or set(notes) - set(evidence):
            raise IntakeError("Source notes must name at most ten declared evidence references")
        for reference, excerpt in notes.items():
            text(reference, "source note reference")
            text(excerpt, "source note", limit=1500)
        ignored = _strings(data.get("ignored_fields", []), "ignored_fields")
        if set(ignored) - set(headers) or set(ignored) & set(mapping.values()):
            raise IntakeError("Ignored fields must be present and cannot also be mapped")
        if ignored and not evidence:
            raise IntakeError("Explicitly ignored fields require a source evidence reference")
        prepared: dict[str, list[dict[str, Any]]] = {}
        for field, operations in transforms.items():
            if not isinstance(operations, list) or len(operations) > 16:
                raise IntakeError("At most 16 operations per mapped field")
            prepared[field] = []
            for operation in operations:
                op = {"op": operation} if isinstance(operation, str) else operation
                if not isinstance(op, dict):
                    raise IntakeError("Transform must be a declared operation")
                name = op.get("op")
                if not isinstance(name, str):
                    raise IntakeError("Operation name must be text")
                if name in OPS and set(op) == {"op"}:
                    if name in {"phone_us", "zip_us", "state_us"}:
                        allowed = {
                            "phone_us": {"phone", "fax"},
                            "zip_us": {"zip"},
                            "state_us": {"state"},
                        }
                        if field not in allowed[name]:
                            raise IntakeError(f"{name} cannot transform {field}")
                elif name == "date" and set(op) == {"op", "format"}:
                    if (
                        field not in DATE_FIELDS
                        or not isinstance(op["format"], str)
                        or op["format"] not in DATE_FORMATS
                    ):
                        raise IntakeError(
                            "Date operation requires a date field and explicit format"
                        )
                elif name == "value_map" and set(op) == {"op", "values"}:
                    values = op["values"]
                    if not evidence or not isinstance(values, dict) or not 1 <= len(values) <= 64:
                        raise IntakeError(
                            "Value maps require bounded enumerations and evidence references"
                        )
                    if any(not isinstance(k, str) or not k or len(k) > 256 for k in values):
                        raise IntakeError("Value-map keys must be bounded nonblank text")
                    if field in BOOLEAN_FIELDS:
                        if any(type(v) is not bool for v in values.values()):
                            raise IntakeError("Visibility maps must produce booleans")
                    elif any(not isinstance(v, str) or len(v) > 256 for v in values.values()):
                        raise IntakeError("Non-boolean maps must produce bounded text")
                else:
                    raise IntakeError("Unsupported operation or arguments")
                prepared[field].append(json.loads(canonical(op)))
        for field in BOOLEAN_FIELDS & mapping.keys():
            operations = prepared.get(field, [])
            if not operations or operations[-1]["op"] != "value_map":
                raise IntakeError("Visibility requires an explicit final value_map with evidence")
        required = _strings(data.get("required", []), "required")
        if set(required) - FIELDS:
            raise IntakeError("Unknown required field")
        conditional = data.get("required_when", [])
        if not isinstance(conditional, list) or len(conditional) > 16:
            raise IntakeError("Too many conditional requirements")
        for rule in conditional:
            rule = object_keys(rule, {"field", "equals", "require"}, {"field", "equals", "require"})
            if (
                not isinstance(rule["field"], str)
                or rule["field"] not in mapping
                or not isinstance(rule["equals"], str)
            ):
                raise IntakeError(
                    "Conditional requirement needs a mapped classifier and text equality"
                )
            text(rule["equals"], "conditional equality")
            if set(_strings(rule["require"], "require")) - FIELDS:
                raise IntakeError("Unknown conditionally required field")
        settings = data.get("settings", {"delimiter": ",", "encoding": "utf-8-sig"})
        settings = object_keys(settings, {"delimiter", "encoding"}, {"delimiter", "encoding"})
        if (
            not isinstance(settings["delimiter"], str)
            or not isinstance(settings["encoding"], str)
            or settings["delimiter"] not in {",", ";", "\t", "|"}
            or settings["encoding"]
            not in {
                "utf-8",
                "utf-8-sig",
                "cp1252",
            }
        ):
            raise IntakeError("Unsupported CSV settings")
        definition = {
            "version": 1,
            "source": context.source,
            "payer": context.payer,
            "network": context.network,
            "source_version": context.source_version,
            "contract": data["contract"],
            "grain": data["grain"],
            "headers": sorted(headers),
            "mapping": mapping,
            "transforms": prepared,
            "required": sorted(required),
            "required_when": conditional,
            "evidence_refs": sorted(evidence),
            "source_notes": notes,
            "ignored_fields": sorted(ignored),
            "settings": settings,
        }
        return cls(canonical(definition))

    @property
    def id(self) -> str:
        return digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self.definition_json)  # type: ignore[no-any-return]


def _operation(value: Any, op: dict[str, Any]) -> Any:
    name = op["op"]
    if value is None or value == "":
        return value
    if not isinstance(value, str):
        raise IntakeError("Text transform received a non-text value")
    if name == "trim":
        return value.strip()
    if name == "collapse_spaces":
        return " ".join(value.split())
    if name == "uppercase":
        return value.upper()
    if name == "lowercase":
        return value.lower()
    if name == "value_map":
        if value not in op["values"]:
            raise IntakeError("Unrecognized source value; no implicit replacement")
        return op["values"][value]
    if name == "phone_us":
        if not re.fullmatch(
            r"(?:\+1[ .-]?)?(?:\([0-9]{3}\)|[0-9]{3})[ .-]?[0-9]{3}[ .-]?[0-9]{4}", value
        ):
            raise IntakeError(
                "Expected an explicit ten-digit US phone; extensions require a separate field"
            )
        digits = re.sub(r"[^0-9]", "", value)
        return digits[-10:]
    if name == "zip_us":
        if not re.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", value):
            raise IntakeError("Expected five-digit ZIP or ZIP+4")
        return value
    if name == "state_us":
        if value.upper() not in STATES:
            raise IntakeError("Unknown US state/territory abbreviation")
        return value.upper()
    if name == "date":
        pattern = (
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
            if op["format"] == "%Y-%m-%d"
            else r"[0-9]{2}/[0-9]{2}/[0-9]{4}"
        )
        if not re.fullmatch(pattern, value):
            raise IntakeError("Date does not match the declared format")
        try:
            return datetime.strptime(value, op["format"]).date().isoformat()
        except ValueError as exc:
            raise IntakeError("Invalid calendar date") from exc
    raise IntakeError("Unknown operation")


@dataclass(slots=True)
class WorkBudget:
    """Bound total text-transform work, including repeated operations over large cells."""

    limit: int = 100_000_000
    consumed: int = 0

    def consume(self, value: Any) -> None:
        self.consumed += max(1, len(value)) if isinstance(value, str) else 1
        if self.consumed > self.limit:
            raise ResourceLimit("Interpretation exceeds the 100 million character-operation budget")


def detail_cost(row: dict[str, Any]) -> int:
    """Conservative retained-detail estimate; rule definitions live once in the variant."""
    cost = 512
    for item in row["lineage"].values():
        cost += 512
        for value in (item["original"], item["value"]):
            cost += 4 * len(value) if isinstance(value, str) else 16
        for step in item["steps"]:
            cost += 256
            for value in (step["before"], step["after"]):
                cost += 4 * len(value) if isinstance(value, str) else 16
    return cost


@dataclass(slots=True)
class PreparedRecipe:
    definition: dict[str, Any]
    required: set[str]

    @classmethod
    def compile(cls, variant: Variant) -> PreparedRecipe:
        # Revalidate at the trusted execution boundary, once for the entire input.
        definition = Variant.from_dict(variant.to_dict()).to_dict()
        return cls(
            definition,
            set(contract(definition["contract"])["required"]) | set(definition["required"]),
        )

    def transform(
        self,
        original: dict[str, Any],
        locator: str,
        findings: Findings,
        budget: WorkBudget | None = None,
    ) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        budget = budget if budget is not None else WorkBudget()
        lineage: dict[str, Any] = {}
        for target, source in self.definition["mapping"].items():
            present = source in original
            raw = original.get(source)
            value = raw
            steps = []
            for operation_index, operation in enumerate(
                self.definition["transforms"].get(target, [])
            ):
                budget.consume(value)
                before = value
                try:
                    value = _operation(value, operation)
                    error = None
                except IntakeError as exc:
                    error = str(exc)
                    findings.add(
                        Finding(
                            "invalid_transform",
                            "structure",
                            f"{locator}[{json.dumps(source)}]",
                            error,
                        )
                    )
                steps.append(
                    {
                        "operation": {
                            "op": operation["op"],
                            "definition_ref": f"/transforms/{target}/{operation_index}",
                        },
                        "before": before,
                        "after": value,
                        "error": error,
                    }
                )
                if error:
                    break
            normalized[target] = value
            lineage[target] = {
                "source_field": source,
                "locator": f"{locator}[{json.dumps(source)}]",
                "original_state": "absent"
                if not present
                else "null"
                if raw is None
                else "blank"
                if raw == ""
                else "value",
                "original": raw,
                "value": value,
                "steps": steps,
            }
            if not present:
                findings.add(
                    Finding(
                        "missing_column",
                        "structure",
                        locator,
                        f"Mapped source field is absent: {source}",
                    )
                )
            if value not in (None, ""):
                if target in BOOLEAN_FIELDS and type(value) is not bool:
                    findings.add(
                        Finding(
                            "invalid_boolean",
                            "structure",
                            locator,
                            f"{target} has no resolved boolean interpretation",
                        )
                    )
                elif target in IDENTIFIER_FIELDS and (
                    not isinstance(value, str) or not re.fullmatch(r"[0-9]{10}", value)
                ):
                    findings.add(
                        Finding(
                            "invalid_npi_shape",
                            "structure",
                            locator,
                            f"{target} requires ten text digits; registry existence not assessed",
                        )
                    )
                elif target in IDENTIFIER_FIELDS and not is_valid_npi(value):
                    findings.add(
                        Finding(
                            "invalid_npi_checksum",
                            "structure",
                            locator,
                            f"{target} fails CMS Luhn check-digit validation; registry existence not assessed",
                        )
                    )
                elif target in DATE_FIELDS and (
                    not isinstance(value, str)
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
                ):
                    findings.add(
                        Finding(
                            "unresolved_date_format",
                            "structure",
                            locator,
                            f"{target} requires an explicit calendar-date interpretation",
                        )
                    )
        required = self.required.copy()
        for rule in self.definition["required_when"]:
            if normalized.get(rule["field"]) in (None, ""):
                findings.add(
                    Finding(
                        "condition_unassessed",
                        "source_contract",
                        locator,
                        f"Conditional requirement cannot be assessed without {rule['field']}",
                    )
                )
            if normalized.get(rule["field"]) == rule["equals"]:
                required.update(rule["require"])
        for target in sorted(required):
            if (
                normalized.get(target) is None
                or normalized.get(target) == ""
                or (isinstance(normalized.get(target), str) and not normalized[target].strip())
            ):
                findings.add(
                    Finding(
                        "required", "source_contract", locator, f"Missing required field: {target}"
                    )
                )
        for target in DATE_FIELDS & normalized.keys():
            value = normalized[target]
            if value:
                try:
                    date.fromisoformat(value)
                except (ValueError, TypeError):
                    findings.add(
                        Finding(
                            "invalid_calendar_date",
                            "structure",
                            locator,
                            f"{target} is not an interpreted ISO calendar date",
                        )
                    )
        if (
            normalized.get("effective_start")
            and normalized.get("effective_end")
            and normalized["effective_start"] > normalized["effective_end"]
        ):
            findings.add(
                Finding(
                    "invalid_period", "source_contract", locator, "Effective start is after end"
                )
            )
        return {"locator": locator, "normalized": normalized, "lineage": lineage}
