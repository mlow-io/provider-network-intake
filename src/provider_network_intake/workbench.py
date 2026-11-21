"""Optional provider-directory host bridge. Core APIs remain independent of FastAPI and host storage."""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from .catalog_store import CatalogStore
from .contracts import FIELDS
from .engine import Engine
from .errors import Conflict, IntakeError, ResourceLimit
from .jsonio import MAX_BYTES, bytes_digest, digest, object_keys, text
from .models import Inspection, SourceContext
from .recipes import Variant


class IntakeHost(Protocol):
    root: Path
    artifacts: Path

    def detail(self, receipt_id: str) -> dict[str, Any]: ...
    def db(self) -> sqlite3.Connection: ...


class WorkbenchBridge:
    """Read retained CSVs and review source variants; never alter operational observations."""

    def __init__(self, host: IntakeHost, catalog: CatalogStore):
        self.host, self.catalog, self.engine = host, catalog, Engine()

    @contextmanager
    def _host_guard(self) -> Iterator[None]:
        # Host -> catalog is the only lock order. The host attempt cannot change
        # between a freshness check and a catalog publication in the other file.
        conn = self.host.db()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                yield
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise Conflict("Host intake is busy; reload the current interpretation") from exc
            raise
        finally:
            conn.close()

    def _input(
        self, receipt_id: str, expected_attempt: str, options: dict[str, Any] | None = None
    ) -> tuple[bytes, Inspection]:
        text(expected_attempt, "expected_attempt")
        detail = self.host.detail(receipt_id)
        if detail.get("attempt_id") != expected_attempt:
            raise Conflict("Host interpretation changed; reopen the current attempt")
        if Path(detail.get("label", "")).suffix.lower() != ".csv":
            raise IntakeError("Source-schema review currently supports retained CSV deliveries")
        sha = detail.get("artifact_sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise IntakeError("A retained original artifact is required")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            with os.fdopen(os.open(self.host.artifacts / sha, flags), "rb") as stream:
                payload = stream.read(MAX_BYTES + 1)
        except OSError as exc:
            raise IntakeError(
                "Retained original artifact is unavailable or unsafe to read"
            ) from exc
        if len(payload) > MAX_BYTES:
            raise ResourceLimit("Retained CSV exceeds the declared byte bound")
        if bytes_digest(payload) != sha:
            raise IntakeError("Retained artifact digest mismatch")
        options = object_keys(
            {} if options is None else options,
            {"payer", "network", "source_version", "completeness"},
            set(),
        )
        context = SourceContext.from_dict(
            {
                "source": detail["source"],
                "delivery_id": f"{receipt_id}:{expected_attempt}",
                **options,
            }
        )
        attempts = detail.get("attempts")
        if (
            not isinstance(attempts, list)
            or not attempts
            or not isinstance(attempts[-1].get("spec"), dict)
        ):
            raise IntakeError("Current host parsing settings are unavailable")
        settings = attempts[-1]["spec"]
        csv_settings = {
            "delimiter": settings.get("delimiter", ","),
            "encoding": settings.get("encoding", "utf-8-sig"),
        }
        return payload, self.engine.inspect(payload, context=context, settings=csv_settings)

    def inspect(
        self, receipt_id: str, expected_attempt: str, options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        _, inspection = self._input(receipt_id, expected_attempt, options)
        snapshot = self.catalog.snapshot(inspection.context.source)
        recognition = self.engine.recognize(inspection, catalog=snapshot)
        draft = self.engine.propose_adaptation(
            inspection, recognition=recognition, catalog=snapshot
        )
        candidates = {candidate["variant_id"] for candidate in recognition.candidates}
        variants = [
            {"id": variant.id, "definition": variant.to_dict()}
            for variant in snapshot.variants
            if variant.id in candidates
        ]
        pending = self.catalog.pending(
            source=inspection.context.source,
            artifact_sha256=inspection.artifact_sha256,
            delivery_id=inspection.context.delivery_id,
        )
        return {
            "inspection": inspection.to_dict(),
            "recognition": recognition.to_dict(),
            "draft": draft["variant"],
            "variants": variants,
            "catalog_revision": snapshot.revision,
            "pending": [
                {key: proposal[key] for key in ("id", "origin", "catalog_revision", "request_id")}
                for proposal in pending
            ],
            "fields": sorted(FIELDS),
            "host_attempt": expected_attempt,
            "notice": "Reviewing a source format does not accept this delivery or change an approved dataset.",
        }

    def preview(
        self,
        receipt_id: str,
        expected_attempt: str,
        definition: dict[str, Any],
        expected_catalog_revision: int,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload, inspection = self._input(receipt_id, expected_attempt, options)
        snapshot = self.catalog.snapshot(inspection.context.source)
        if snapshot.revision != expected_catalog_revision:
            raise Conflict("Catalog changed; reload candidates and preview again")
        variant = Variant.from_dict(definition)
        proposal = self.engine.proposal(inspection, variant, catalog_revision=snapshot.revision)
        report = self.engine.preview(
            payload,
            proposal=proposal,
            context=inspection.context,
            detail_limit=10,
            finding_limit=50,
        )
        token = digest(
            {
                "attempt": expected_attempt,
                "artifact": report["artifact_sha256"],
                "variant": report["variant_id"],
                "catalog_revision": snapshot.revision,
                "context": report["context"],
                "findings": report["findings"]["counts"],
                "eligibility": report["eligibility"],
            }
        )
        clipped = False

        def sample(value: Any) -> Any:
            nonlocal clipped
            if isinstance(value, str) and len(value) > 240:
                clipped = True
                return value[:240] + "…"
            if isinstance(value, dict):
                return {key: sample(item) for key, item in value.items()}
            if isinstance(value, list):
                return [sample(item) for item in value]
            return value

        report["rows"] = sample(report["rows"])
        report["samples_clipped"] = clipped
        return {
            "report": report,
            "preview_token": token,
            "host_attempt": expected_attempt,
            "catalog_revision": snapshot.revision,
        }

    def propose(
        self,
        receipt_id: str,
        expected_attempt: str,
        definition: dict[str, Any],
        expected_catalog_revision: int,
        preview_token: str,
        request_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._host_guard():
            preview = self.preview(
                receipt_id, expected_attempt, definition, expected_catalog_revision, options
            )
            if preview_token != preview["preview_token"]:
                raise Conflict("Preview changed; inspect its current values before saving")
            _, inspection = self._input(receipt_id, expected_attempt, options)
            return self.catalog.propose(
                inspection,
                Variant.from_dict(definition),
                origin="manual",
                request_id=request_id,
                expected_revision=expected_catalog_revision,
            )

    def proposal(self, receipt_id: str, expected_attempt: str, proposal_id: str) -> dict[str, Any]:
        proposal = self.catalog.proposal(proposal_id)
        context = SourceContext.from_dict(proposal["context"])
        options = {
            key: getattr(context, key)
            for key in ("payer", "network", "source_version", "completeness")
        }
        _, inspection = self._input(receipt_id, expected_attempt, options)
        if (
            proposal["context"] != inspection.to_dict()["context"]
            or proposal["artifact_sha256"] != inspection.artifact_sha256
        ):
            raise Conflict("Proposal belongs to a different retained delivery/attempt")
        return proposal

    def review(
        self,
        receipt_id: str,
        expected_attempt: str,
        proposal_id: str,
        *,
        actor: str,
        reason: str,
        expected_catalog_revision: int,
        preview_token: str,
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        with self._host_guard():
            proposal = self.proposal(receipt_id, expected_attempt, proposal_id)
            context = SourceContext.from_dict(proposal["context"])
            options = {
                key: getattr(context, key)
                for key in ("payer", "network", "source_version", "completeness")
            }
            current = self.preview(
                receipt_id,
                expected_attempt,
                proposal["variant"],
                expected_catalog_revision,
                options,
            )
            if current["preview_token"] != preview_token:
                raise Conflict(
                    "Review requires a preview of this exact proposal and current context"
                )
            return self.catalog.review(
                proposal_id,
                actor=actor,
                reason=reason,
                expected_revision=expected_catalog_revision,
                supersedes=supersedes,
            )


def mount(app: Any, host: IntakeHost, catalog: CatalogStore) -> WorkbenchBridge:
    """Opt-in FastAPI mounting. Importing the package never imports the host framework."""
    from fastapi import APIRouter, HTTPException

    bridge = WorkbenchBridge(host, catalog)
    router = APIRouter(prefix="/api/intakes/{receipt_id}/source-schema", tags=["source-schema"])

    def call(action: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return action(*args, **kwargs)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except IntakeError as exc:
            raise HTTPException(422, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, "Retained input or proposal not found") from exc

    @router.post("/inspect")
    def inspect_source(receipt_id: str, payload: dict[str, Any]) -> Any:
        call(object_keys, payload, {"expected_attempt", "context"}, {"expected_attempt"})
        return call(bridge.inspect, receipt_id, payload["expected_attempt"], payload.get("context"))

    @router.post("/preview")
    def preview_source(receipt_id: str, payload: dict[str, Any]) -> Any:
        call(
            object_keys,
            payload,
            {"expected_attempt", "context", "definition", "expected_catalog_revision"},
            {"expected_attempt", "definition", "expected_catalog_revision"},
        )
        return call(
            bridge.preview,
            receipt_id,
            payload["expected_attempt"],
            payload["definition"],
            payload["expected_catalog_revision"],
            payload.get("context"),
        )

    @router.post("/proposals")
    def propose_source(receipt_id: str, payload: dict[str, Any]) -> Any:
        call(
            object_keys,
            payload,
            {
                "expected_attempt",
                "context",
                "definition",
                "expected_catalog_revision",
                "preview_token",
                "request_id",
            },
            {
                "expected_attempt",
                "definition",
                "expected_catalog_revision",
                "preview_token",
                "request_id",
            },
        )
        return call(
            bridge.propose,
            receipt_id,
            payload["expected_attempt"],
            payload["definition"],
            payload["expected_catalog_revision"],
            payload["preview_token"],
            payload["request_id"],
            payload.get("context"),
        )

    @router.post("/proposals/{proposal_id}/inspect")
    def inspect_proposal(receipt_id: str, proposal_id: str, payload: dict[str, Any]) -> Any:
        call(object_keys, payload, {"expected_attempt"}, {"expected_attempt"})
        return call(bridge.proposal, receipt_id, payload["expected_attempt"], proposal_id)

    @router.post("/proposals/{proposal_id}/review")
    def review_proposal(receipt_id: str, proposal_id: str, payload: dict[str, Any]) -> Any:
        call(
            object_keys,
            payload,
            {
                "expected_attempt",
                "actor",
                "reason",
                "expected_catalog_revision",
                "preview_token",
                "supersedes",
            },
            {"expected_attempt", "actor", "reason", "expected_catalog_revision", "preview_token"},
        )
        return call(
            bridge.review,
            receipt_id,
            payload["expected_attempt"],
            proposal_id,
            actor=payload["actor"],
            reason=payload["reason"],
            expected_catalog_revision=payload["expected_catalog_revision"],
            preview_token=payload["preview_token"],
            supersedes=payload.get("supersedes"),
        )

    app.include_router(router)
    return bridge
