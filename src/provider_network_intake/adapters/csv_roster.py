"""Bounded text-faithful CSV reading. No numeric, null or formula inference."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from typing import Any

from ..errors import IntakeError, ResourceLimit
from ..jsonio import MAX_BYTES, bytes_digest
from ..models import Inspection, SourceContext

MAX_ROWS = 10_000
MAX_COLUMNS = 64
MAX_CELL = 8192


def rows(
    payload: bytes, *, delimiter: str = ",", encoding: str = "utf-8-sig"
) -> Iterator[tuple[int, dict[str, str]]]:
    if not payload or len(payload) > MAX_BYTES:
        raise ResourceLimit("CSV must contain 1 byte to 10 MiB")
    if (
        not isinstance(delimiter, str)
        or not isinstance(encoding, str)
        or delimiter not in {",", ";", "\t", "|"}
        or encoding not in {"utf-8", "utf-8-sig", "cp1252"}
    ):
        raise IntakeError("Unsupported explicit CSV delimiter/encoding")
    try:
        with io.StringIO(payload.decode(encoding), newline="") as stream:
            reader = csv.reader(stream, delimiter=delimiter, strict=True)
            headers = next(reader, [])
            if not 1 <= len(headers) <= MAX_COLUMNS or any(
                not h.strip() or len(h) > 256 or "\x00" in h for h in headers
            ):
                raise IntakeError("CSV requires 1–64 nonblank headers of at most 256 characters")
            if len(set(headers)) != len(headers):
                raise IntakeError("Duplicate CSV headers")
            yield 0, dict.fromkeys(headers, "")
            for index, values in enumerate(reader, 1):
                if index > MAX_ROWS:
                    raise ResourceLimit("CSV exceeds 10000 data rows")
                if len(values) != len(headers):
                    raise IntakeError(
                        f"CSV record {index}: expected {len(headers)} cells, got {len(values)}"
                    )
                if any(len(value) > MAX_CELL or "\x00" in value for value in values):
                    raise ResourceLimit(f"CSV record {index}: cell size/NUL limit")
                yield index, dict(zip(headers, values, strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise IntakeError(f"Invalid CSV: {exc}") from exc


def inspect(payload: bytes, context: SourceContext, **settings: Any) -> Inspection:
    headers: tuple[str, ...] = ()
    count = 0
    missing: dict[str, int] = {}
    for index, row in rows(payload, **settings):
        if index == 0:
            headers = tuple(row)
            missing = dict.fromkeys(headers, 0)
        else:
            count += 1
            for key, value in row.items():
                missing[key] += not value.strip()
    descriptor = {
        "fields": [
            {"name": h, "observed_types": ["string"], "blank_count": missing[h]} for h in headers
        ],
        "sample_policy": "headers_and_aggregate_counts_only",
        "coverage": "complete_within_limits",
    }
    return Inspection(
        context,
        bytes_digest(payload),
        "csv",
        headers,
        count,
        descriptor,
        {
            "delimiter": settings.get("delimiter", ","),
            "encoding": settings.get("encoding", "utf-8-sig"),
        },
    )
