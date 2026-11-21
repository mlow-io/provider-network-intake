"""Strict bounded JSON and portable semantic hashes. No implicit filesystem or network I/O."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from typing import Any

from .errors import IntakeError, ResourceLimit

MAX_BYTES = 10 * 1024 * 1024


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def bytes_digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntakeError(f"Duplicate JSON property: {key}")
        result[key] = value
    return result


def _constant(value: str) -> Any:
    raise IntakeError(f"Invalid JSON constant: {value}")


def walk(value: Any, path: str = "$", *, max_nodes: int = 100_000) -> Iterator[tuple[str, Any]]:
    stack = [(path, value, 0)]
    nodes = 0
    while stack:
        at, node, depth = stack.pop()
        nodes += 1
        if depth > 64 or nodes > max_nodes:
            raise ResourceLimit(f"JSON exceeds depth/node limits (64/{max_nodes})")
        yield at, node
        if isinstance(node, dict):
            if nodes + len(stack) + len(node) > max_nodes:
                raise ResourceLimit(f"JSON exceeds depth/node limits (64/{max_nodes})")
            stack.extend(
                (f"{at}[{json.dumps(key)}]", child, depth + 1) for key, child in node.items()
            )
        elif isinstance(node, list):
            if nodes + len(stack) + len(node) > max_nodes:
                raise ResourceLimit(f"JSON exceeds depth/node limits (64/{max_nodes})")
            stack.extend((f"{at}[{i}]", child, depth + 1) for i, child in enumerate(node))


def loads(payload: bytes, *, max_bytes: int = MAX_BYTES, max_nodes: int = 100_000) -> Any:
    if not payload or len(payload) > max_bytes:
        raise ResourceLimit(f"Expected 1–{max_bytes} JSON bytes")
    try:
        value = json.loads(
            payload.decode("utf-8-sig"), object_pairs_hook=_object, parse_constant=_constant
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise IntakeError(f"Invalid JSON: {exc}") from exc
    for _, node in walk(value, max_nodes=max_nodes):
        if isinstance(node, float) and not math.isfinite(node):
            raise IntakeError("JSON numbers must be finite")
    return value


def object_keys(value: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise IntakeError(
            f"Expected object with required keys {sorted(required)}; allowed {sorted(allowed)}"
        )
    return value


def text(value: Any, name: str, *, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IntakeError(f"{name} must be nonblank text of at most {limit} characters")
    return value
