"""Small synchronous invocation journal: uncertain remote outcomes are never auto-retried."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from .assistance import ProviderFailure, build_request
from .catalog_store import CatalogSnapshot
from .errors import Conflict, IntakeError
from .jsonio import canonical, digest, text
from .models import Inspection


class ProposalProvider(Protocol):
    model: str

    def invoke(
        self,
        inspection: Inspection,
        catalog: CatalogSnapshot,
        *,
        evidence: dict[str, str],
        external_processing: bool = False,
    ) -> dict[str, Any]: ...


class InvocationJournal:
    """An explicit separate file; no operational data or credential discovery."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        with self._connection() as conn:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if tables and tables != {"invocation", "invocation_event"}:
                raise IntakeError("Not an intake invocation journal")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS invocation (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS invocation_event (
              invocation_id TEXT NOT NULL REFERENCES invocation(id), revision INTEGER NOT NULL,
              state TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(invocation_id,revision)
            );
            """)
            for table in ("invocation", "invocation_event"):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable invocation history'); END"
                    )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _last(conn: sqlite3.Connection, request_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT revision,state,body FROM invocation_event WHERE invocation_id=? ORDER BY revision DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if row is None:
            raise IntakeError("Unknown invocation")
        return {
            "request_id": request_id,
            "revision": row["revision"],
            "state": row["state"],
            **json.loads(row["body"]),
        }

    def start(
        self, request_id: str, context: dict[str, Any], *, model: str
    ) -> tuple[bool, dict[str, Any]]:
        text(request_id, "request_id")
        body = canonical({"model": text(model, "model"), "context": context})
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT body FROM invocation WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != body:
                    raise Conflict(
                        "Invocation ID was already used for a different input/model/catalog context"
                    )
                return False, self._last(conn, request_id)
            conn.execute("INSERT INTO invocation VALUES (?,?)", (request_id, body))
            conn.execute(
                "INSERT INTO invocation_event VALUES (?,1,'running',?)",
                (request_id, canonical({"context_sha256": digest(context)})),
            )
            return True, {"request_id": request_id, "revision": 1, "state": "running"}

    def finish(
        self,
        request_id: str,
        *,
        result: dict[str, Any] | None = None,
        error_type: str | None = None,
        audit_response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._last(conn, request_id)
            if current["state"] == "cancelled":
                # Preserve a late response for audit but never expose it as a usable proposal.
                body = {
                    "result": None,
                    "quarantined_response": result or audit_response,
                    "error_type": error_type,
                    "notice": "Cancellation fenced proposal publication; any completed response is audit-only.",
                }
                state = "cancelled"
            elif current["state"] != "running":
                raise Conflict("Invocation has already reached a terminal state")
            elif error_type:
                state = "failed"
                body = {
                    "result": None,
                    "error_type": error_type,
                    "external_outcome": "unknown",
                    "quarantined_response": audit_response,
                    "notice": "No automatic retry; a request may have reached the external provider.",
                }
            else:
                state = "completed"
                body = {"result": result}
            revision = current["revision"] + 1
            conn.execute(
                "INSERT INTO invocation_event VALUES (?,?,?,?)",
                (request_id, revision, state, canonical(body)),
            )
            return {"request_id": request_id, "revision": revision, "state": state, **body}

    def cancel(self, request_id: str, *, expected_revision: int, reason: str) -> dict[str, Any]:
        text(reason, "reason", limit=2000)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._last(conn, request_id)
            if current["revision"] != expected_revision or current["state"] != "running":
                raise Conflict("Invocation changed or is terminal")
            revision = expected_revision + 1
            body = {
                "result": None,
                "reason": reason,
                "notice": "Proposal publication cancelled; an in-flight external call may still finish or be billed.",
            }
            conn.execute(
                "INSERT INTO invocation_event VALUES (?,?,'cancelled',?)",
                (request_id, revision, canonical(body)),
            )
            return {"request_id": request_id, "revision": revision, "state": "cancelled", **body}

    def get(self, request_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            return self._last(conn, request_id)


def run_assistance(
    journal: InvocationJournal,
    *,
    request_id: str,
    provider: ProposalProvider,
    inspection: Inspection,
    catalog: CatalogSnapshot,
    evidence: dict[str, str],
    external_processing: bool = False,
) -> dict[str, Any]:
    if external_processing is not True:
        raise IntakeError("External processing must be explicitly enabled for this invocation")
    context = build_request(inspection, catalog, evidence=evidence)
    created, current = journal.start(request_id, context, model=provider.model)
    if not created:
        if current["state"] == "running":
            raise Conflict(
                "Prior invocation is running or has an unknown external outcome; no automatic replay"
            )
        return current
    try:
        result = provider.invoke(inspection, catalog, evidence=evidence, external_processing=True)
    except Exception as exc:
        # Store no exception text: custom transports/providers can include secrets in messages.
        return journal.finish(
            request_id,
            error_type=type(exc).__name__,
            audit_response=exc.receipt if isinstance(exc, ProviderFailure) else None,
        )
    return journal.finish(request_id, result=result)
