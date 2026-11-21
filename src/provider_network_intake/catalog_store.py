"""Explicit, local SQLite catalog. Originals and operational approvals remain host-owned."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import Conflict, IntakeError, ResourceLimit
from .jsonio import canonical, digest, text
from .models import Inspection, definition_scope
from .recipes import Variant

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('revision', 0);
CREATE TABLE IF NOT EXISTS observation (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposal (
 id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL,
 variant TEXT NOT NULL, artifact_sha TEXT NOT NULL, source TEXT NOT NULL,
 catalog_revision INTEGER NOT NULL, origin TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS variant_revision (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS variant_source ON variant_revision(source);
CREATE TABLE IF NOT EXISTS review_event (
 revision INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL UNIQUE REFERENCES proposal(id),
 variant_id TEXT REFERENCES variant_revision(id), supersedes TEXT REFERENCES variant_revision(id),
 disposition TEXT NOT NULL CHECK(disposition IN ('reviewed','rejected')),
 actor TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    revision: int
    variants: tuple[Variant, ...]

    @property
    def id(self) -> str:
        return digest({"revision": self.revision, "variants": [v.id for v in self.variants]})


class CatalogStore:
    """Creating this object explicitly creates/opens its catalog, never a host database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) == ":memory:":
            raise IntakeError(
                "Use an explicit file for a durable catalog, or CatalogSnapshot for stateless work"
            )
        with self._connection() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise IntakeError("Unsupported catalog schema version")
            # An unrelated database is never adopted just because its user_version is zero.
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            expected = {"meta", "observation", "proposal", "variant_revision", "review_event"}
            if tables and tables != expected:
                raise IntakeError("Not a provider-network-intake catalog")
            conn.executescript(SCHEMA)
            for table in sorted(expected - {"meta"}):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Immutable catalog record'); END"
                    )
            conn.execute("PRAGMA user_version=1")

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
    def _revision(conn: sqlite3.Connection) -> int:
        return int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def snapshot(self, source: str | None = None) -> CatalogSnapshot:
        with self._connection() as conn:
            conn.execute("BEGIN")
            revision = self._revision(conn)
            query = """SELECT DISTINCT v.id, v.body FROM variant_revision v
            JOIN review_event r ON r.variant_id=v.id AND r.disposition='reviewed'
            WHERE NOT EXISTS (SELECT 1 FROM review_event s WHERE s.supersedes=v.id)"""
            params: tuple[str, ...] = ()
            if source is not None:
                query += " AND v.source=?"
                params = (source,)
            rows = conn.execute(query + " ORDER BY v.id LIMIT 1001", params).fetchall()
            if len(rows) > 1000:
                raise ResourceLimit(
                    "Catalog snapshot exceeds 1000 active variants; select a source scope"
                )
            variants = tuple(Variant.from_dict(json.loads(r["body"])) for r in rows)
            if any(variant.id != row["id"] for variant, row in zip(variants, rows, strict=True)):
                raise IntakeError("Stored variant definition digest mismatch")
            return CatalogSnapshot(revision, variants)

    def observe(self, inspection: Inspection) -> str:
        body = inspection.to_dict()
        observation_id = digest(body)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT count(*) FROM observation").fetchone()[0] >= 100_000:
                if not conn.execute(
                    "SELECT 1 FROM observation WHERE id=?", (observation_id,)
                ).fetchone():
                    raise ResourceLimit("Catalog observation limit reached")
            conn.execute(
                "INSERT OR IGNORE INTO observation VALUES (?,?)", (observation_id, canonical(body))
            )
        return observation_id

    def propose(
        self,
        inspection: Inspection,
        variant: Variant,
        *,
        origin: str,
        request_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        text(request_id, "request_id")
        if type(expected_revision) is not int or expected_revision < 0:
            raise IntakeError("Expected revision must be a nonnegative integer")
        if not isinstance(origin, str) or origin not in {
            "manual",
            "heuristic",
            "llm",
            "mcp",
            "import",
        }:
            raise IntakeError("Unknown proposal origin")
        variant = Variant.from_dict(variant.to_dict())
        definition = variant.to_dict()
        if definition_scope(definition) != inspection.context.scope:
            raise IntakeError("Variant source/payer/network context does not match input")
        if (
            set(definition["headers"]) != set(inspection.fields)
            or definition["settings"] != inspection.settings
        ):
            raise IntakeError("Variant headers/settings must describe this exact input")
        body = {
            "version": 1,
            "kind": "new_variant",
            "artifact_sha256": inspection.artifact_sha256,
            "input_signature": inspection.signature,
            "context": inspection.to_dict()["context"],
            "inspection": inspection.to_dict(),
            "variant": definition,
            "origin": origin,
            "request_id": request_id,
            "catalog_revision": expected_revision,
        }
        proposal_id = digest(body)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM proposal WHERE request_id=?", (request_id,)
            ).fetchone()
            if existing:
                if existing[0] != proposal_id:
                    raise Conflict("Request ID was already used for different proposal content")
                return {"id": proposal_id, **body}
            if self._revision(conn) != expected_revision:
                raise Conflict("Catalog changed; inspect candidates and preview again")
            if conn.execute("SELECT count(*) FROM proposal").fetchone()[0] >= 100_000:
                raise ResourceLimit("Catalog proposal limit reached")
            conn.execute(
                "INSERT INTO proposal VALUES (?,?,?,?,?,?,?,?)",
                (
                    proposal_id,
                    request_id,
                    canonical(body),
                    variant.definition_json,
                    inspection.artifact_sha256,
                    inspection.context.source,
                    expected_revision,
                    origin,
                ),
            )
        return {"id": proposal_id, **body}

    def proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT body FROM proposal WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                raise IntakeError("Unknown proposal")
            return {"id": proposal_id, **json.loads(row[0])}

    def pending(
        self,
        *,
        source: str,
        artifact_sha256: str,
        delivery_id: str,
        limit: int = 10,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise IntakeError("Use pending limit 1–100 and offset >=0")
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT p.id,p.body FROM proposal p
              WHERE p.source=? AND p.artifact_sha=? AND json_extract(p.body,'$.context.delivery_id')=?
              AND NOT EXISTS (SELECT 1 FROM review_event e WHERE e.proposal_id=p.id)
              ORDER BY p.id LIMIT ? OFFSET ?""",
                (source, artifact_sha256, delivery_id, limit, offset),
            ).fetchall()
            return [{"id": row["id"], **json.loads(row["body"])} for row in rows]

    def variant(self, variant_id: str) -> Variant:
        """Read an explicitly pinned historical revision, including superseded interpretations."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT body FROM variant_revision WHERE id=?", (variant_id,)
            ).fetchone()
            if row is None:
                raise IntakeError("Unknown reviewed variant")
            variant = Variant.from_dict(json.loads(row[0]))
            if variant.id != variant_id:
                raise IntakeError("Stored variant definition digest mismatch")
            return variant

    def review(
        self,
        proposal_id: str,
        *,
        actor: str,
        reason: str,
        expected_revision: int,
        disposition: str = "reviewed",
        supersedes: str | None = None,
    ) -> dict[str, Any]:
        text(actor, "actor")
        text(reason, "reason", limit=2000)
        if type(expected_revision) is not int or expected_revision < 0:
            raise IntakeError("Expected revision must be a nonnegative integer")
        if supersedes is not None:
            text(supersedes, "supersedes")
        if (
            not isinstance(disposition, str)
            or disposition not in {"reviewed", "rejected"}
            or disposition == "rejected"
            and supersedes
        ):
            raise IntakeError("Invalid review disposition/supersession")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if self._revision(conn) != expected_revision:
                raise Conflict("Catalog changed; review its current revision")
            proposal = conn.execute("SELECT * FROM proposal WHERE id=?", (proposal_id,)).fetchone()
            if proposal is None:
                raise IntakeError("Unknown proposal")
            if conn.execute(
                "SELECT 1 FROM review_event WHERE proposal_id=?", (proposal_id,)
            ).fetchone():
                raise Conflict("Proposal was already reviewed")
            if proposal["catalog_revision"] != expected_revision:
                raise Conflict(
                    "Proposal was prepared against an older catalog; re-propose against current context"
                )
            variant = Variant.from_dict(json.loads(proposal["variant"]))
            if supersedes:
                previous = conn.execute(
                    "SELECT body FROM variant_revision WHERE id=?", (supersedes,)
                ).fetchone()
                if previous is None or supersedes == variant.id:
                    raise IntakeError("Supersession requires a different existing variant")
                prior = json.loads(previous[0])
                new = variant.to_dict()
                if any(prior[k] != new[k] for k in ("source", "payer", "network", "contract")):
                    raise IntakeError("Cannot supersede a variant from a different scope/contract")
                if conn.execute(
                    "SELECT 1 FROM review_event WHERE supersedes=?", (supersedes,)
                ).fetchone():
                    raise Conflict("Variant has already been superseded")
            revision = expected_revision + 1
            variant_id = variant.id if disposition == "reviewed" else None
            if variant_id:
                conn.execute(
                    "INSERT OR IGNORE INTO variant_revision VALUES (?,?,?)",
                    (variant_id, proposal["source"], variant.definition_json),
                )
            at = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO review_event VALUES (?,?,?,?,?,?,?,?)",
                (revision, proposal_id, variant_id, supersedes, disposition, actor, reason, at),
            )
            conn.execute("UPDATE meta SET value=? WHERE key='revision'", (revision,))
        return {
            "revision": revision,
            "proposal_id": proposal_id,
            "variant_id": variant_id,
            "supersedes": supersedes,
            "disposition": disposition,
            "actor": actor,
            "reason": reason,
            "created_at": at,
        }

    def history(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        if (
            type(limit) is not int
            or not 1 <= limit <= 1000
            or type(offset) is not int
            or offset < 0
        ):
            raise IntakeError("Use history limit 1–1000 and offset >=0")
        with self._connection() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM review_event ORDER BY revision LIMIT ? OFFSET ?", (limit, offset)
                )
            ]
