"""Reproducible synthetic resource checks; external validators/models are excluded."""

from __future__ import annotations

import argparse
import csv
import io
import json
import platform
import resource
import statistics
import subprocess
import sys
import time
from dataclasses import replace
from typing import Any

from .catalog_store import CatalogSnapshot
from .contracts import BOOLEAN_FIELDS, DATE_FIELDS, FIELDS, IDENTIFIER_FIELDS
from .engine import Engine
from .models import SourceContext
from .recipes import Variant


def csv_workload() -> tuple[bytes, Variant]:
    fields = sorted(FIELDS)
    headers = fields + [f"source_metadata_{i}" for i in range(64 - len(fields))]
    values = []
    for name in headers:
        if name in IDENTIFIER_FIELDS:
            value = "1000000004"
        elif name in DATE_FIELDS:
            value = "2026-11-21"
        elif name in BOOLEAN_FIELDS:
            value = "Y"
        elif name == "reported_status":
            value = "reported-active"
        else:
            value = "SYNTHETIC-ONLY"
        values.append(value)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(headers)
    for _ in range(10000):
        writer.writerow(values)
    definition = {
        "version": 1,
        "source": "synthetic-benchmark",
        "network": "synthetic-network",
        "contract": "payer-network-roster/1",
        "grain": "practitioner_office",
        "headers": headers,
        "mapping": {f: f for f in fields},
        "transforms": {"directory_visible": [{"op": "value_map", "values": {"Y": True}}]},
        "evidence_refs": ["synthetic-benchmark-source"],
        "ignored_fields": headers[len(fields) :],
    }
    return stream.getvalue().encode(), Variant.from_dict(definition)


def fhir_workload() -> bytes:
    resources: list[dict[str, Any]] = [
        {"resourceType": "Practitioner", "id": "clinician"},
        {"resourceType": "Location", "id": "office"},
    ]
    for i in range(998):
        resources.append(
            {
                "resourceType": "PractitionerRole",
                "id": f"role-{i}",
                "practitioner": {"reference": "Practitioner/clinician"},
                "location": [{"reference": "Location/office"}],
                "text": {"div": "SYNTHETIC" * 1000},
            }
        )
    return json.dumps(
        {
            "resourceType": "Bundle",
            "type": "searchset",
            "entry": [{"resource": r} for r in resources],
        }
    ).encode()


def measure(kind: str, repetitions: int = 5) -> dict[str, Any]:
    engine = Engine()
    context = SourceContext("synthetic-benchmark", "benchmark", network="synthetic-network")
    stages: list[dict[str, float]] = []
    payload, seed = csv_workload() if kind == "csv" else (fhir_workload(), None)
    variants = []
    if seed:
        base = seed.to_dict()
        for i in range(1000):
            definition = {**base, "evidence_refs": [f"synthetic-source-{i}"]}
            variants.append(Variant.from_dict(definition))
    snapshot = CatalogSnapshot(0, tuple(variants))
    for i in range(repetitions + 1):
        started = time.perf_counter()
        inspection = engine.inspect(
            payload,
            context=replace(context, delivery_id=f"benchmark-{i}"),
            format_hint="csv" if kind == "csv" else "plannet",
        )
        inspected = time.perf_counter()
        if kind == "csv":
            recognition = engine.recognize(inspection, catalog=snapshot)
            recognized = time.perf_counter()
            assert seed is not None
            proposal = engine.proposal(inspection, seed, catalog_revision=0)
            report = engine.preview(payload, proposal=proposal, context=inspection.context)
            assert (
                len(recognition.candidates) == 5
            )  # Returned details are capped, all candidates still assessed.
        else:
            recognized = inspected
            report = engine.preview_native(
                payload, context=inspection.context, format_hint="plannet"
            )
        done = time.perf_counter()
        assert report["row_count"] == (10000 if kind == "csv" else 1000)
        assert len(report["rows"]) <= 100
        stages.append(
            {
                "inspect": inspected - started,
                "recognize": recognized - inspected,
                "preview": done - recognized,
                "total": done - started,
            }
        )
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = rss if sys.platform == "darwin" else rss * 1024
    warm = stages[1:]
    maximum = max(stage["total"] for stage in stages)
    return {
        "format_version": 1,
        "workload": kind,
        "python": platform.python_version(),
        "platform": platform.system(),
        "machine": platform.machine(),
        "input_bytes": len(payload),
        "rows_or_entries": 10000 if kind == "csv" else 1000,
        "catalog_variants": len(variants),
        "detail_limit": 100,
        "cold_seconds": stages[0]["total"],
        "warm_median_seconds": statistics.median(s["total"] for s in warm),
        "maximum_seconds": maximum,
        "stages": stages,
        "peak_rss_bytes": rss_bytes,
        "target": {"seconds": 5, "peak_rss_bytes": 256 * 1024 * 1024},
        "passed": maximum <= 5 and rss_bytes <= 256 * 1024 * 1024,
        "scope": "Pure Python bounded preview including input bytes and catalog; no external model/validator or full evidence-export timing.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["csv", "plannet"])
    args = parser.parse_args()
    if args.worker:
        result = measure(args.worker)
        print(json.dumps(result))
        raise SystemExit(0 if result["passed"] else 1)
    results = []
    for kind in ("csv", "plannet"):
        run = subprocess.run(
            [sys.executable, "-m", "provider_network_intake.benchmark", "--worker", kind],
            capture_output=True,
            text=True,
            check=False,
        )
        if not run.stdout:
            raise SystemExit(run.stderr)
        results.append(json.loads(run.stdout))
    print(
        json.dumps({"benchmarks": results, "passed": all(r["passed"] for r in results)}, indent=2)
    )
    raise SystemExit(0 if all(r["passed"] for r in results) else 1)


if __name__ == "__main__":
    main()
