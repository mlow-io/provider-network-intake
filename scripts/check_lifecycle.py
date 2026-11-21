"""Separate allocation/handle check; tracing overhead is excluded from runtime benchmarks."""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tempfile
import tracemalloc
from pathlib import Path

from provider_network_intake import CatalogStore, Engine, SourceContext
from provider_network_intake.benchmark import csv_workload
from provider_network_intake.exports import read_bundle, write_full_bundle
from provider_network_intake.jsonio import digest


def handles():
    proc = Path("/proc/self/fd")
    if proc.is_dir():
        return len(list(proc.iterdir()))
    result = subprocess.run(
        ["lsof", "-a", "-p", str(os.getpid()), "-Ff"], capture_output=True, text=True, check=True
    )
    return sum(line.startswith("f") and line[1:].isdigit() for line in result.stdout.splitlines())


def main():
    engine, context = (
        Engine(),
        SourceContext("synthetic-benchmark", "lifecycle", network="synthetic-network"),
    )
    large, variant = csv_workload()
    payload = b"\n".join(large.splitlines()[:501]) + b"\n"
    del large
    with tempfile.TemporaryDirectory(prefix="intake-lifecycle-") as directory:
        root = Path(directory)
        store = CatalogStore(root / "catalog.sqlite3")
        inspection = engine.inspect(payload, context=context)
        proposal = engine.proposal(inspection, variant, catalog_revision=0)
        write_full_bundle(root / "bundle", payload, context=context, proposal=proposal)
        expected = digest(engine.preview(payload, proposal=proposal, context=context))
        tracemalloc.start()
        samples = []
        for index in range(25):
            report = engine.preview(payload, proposal=proposal, context=context)
            assert digest(report) == expected
            reopened = CatalogStore(store.path)
            assert reopened.snapshot().revision == 0
            imported = read_bundle(root / "bundle")
            assert imported["import_state"] == "proposal_only"
            del report, reopened, imported
            gc.collect()
            live, peak = tracemalloc.get_traced_memory()
            samples.append(live)
            if index == 4:
                baseline, baseline_handles = live, handles()
        final_handles = handles()
        growth = samples[-1] - baseline
        result = {
            "format_version": 1,
            "warmup": 5,
            "repetitions": 20,
            "rows_per_import": 500,
            "operation": "inspect-bound preview + streamed-bundle verification + SQLite reopen",
            "instrumentation": "tracemalloc in a separate process from latency benchmark",
            "live_after_warmup_bytes": baseline,
            "live_after_20_bytes": samples[-1],
            "live_growth_bytes": growth,
            "traced_peak_bytes": peak,
            "handles_before": baseline_handles,
            "handles_after": final_handles,
            "target": {"live_growth_bytes": 1048576, "handle_growth": 2},
            "passed": growth < 1048576 and final_handles <= baseline_handles + 2,
        }
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
