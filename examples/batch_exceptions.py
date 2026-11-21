"""Independent batch consumer: a portable exception report without Workbench or a model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from provider_network_intake import CatalogStore, Engine, SourceContext
from provider_network_intake.exports import exceptions_csv, write_full_bundle
from provider_network_intake.jsonio import MAX_BYTES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--source", required=True)
    parser.add_argument("--delivery", required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    with args.file.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    context, engine = SourceContext(args.source, args.delivery), Engine()
    catalog = CatalogStore(args.catalog).snapshot(context.source)
    inspection = engine.inspect(payload, context=context)
    recognition = engine.recognize(inspection, catalog=catalog)
    proposal = engine.propose_adaptation(inspection, recognition=recognition, catalog=catalog)
    report = engine.preview(payload, proposal=proposal, context=context)
    args.output.mkdir(mode=0o700, exist_ok=False)
    (args.output / "exceptions.csv").write_text(exceptions_csv(report), encoding="utf-8")
    write_full_bundle(args.output / "evidence", payload, context=context, proposal=proposal)
    print(
        json.dumps(
            {
                "recognition": recognition.status,
                "eligibility": report["eligibility"],
                "approval": report["approval"],
                "rows": report["row_count"],
            }
        )
    )


if __name__ == "__main__":
    main()
