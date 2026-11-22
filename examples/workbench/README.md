# Optional Workbench integration

The package runs independently of Provider Directory Workbench. This example adds a
source-format review panel to that existing analyst application. `Engine` and
`CatalogStore` can instead be embedded in any Python application; no Workbench database
or frontend is required. See [API](../../docs/API.md).

The host patch targets the earlier Provider Directory Workbench base commit `52a6f4f`. In the public [Provider Data Workbench](https://github.com/mlow-io/provider-data-workbench), `provider-network-intake` can be installed directly alongside the FastAPI backend (`pip install -e .`) and auto-mounted into the operator UI without applying a separate patch. To test the standalone example patch against the older base revision:
Install the built library wheel into the interpreter used to launch that host. From the
Workbench checkout, with `INTAKE_CHECKOUT` set to this library's directory:

```sh
python -m pip install "$INTAKE_CHECKOUT"/dist/*.whl
# Workbench's normal backend/frontend dependencies are also required.
git apply --check "$INTAKE_CHECKOUT/examples/workbench/host.patch"
git apply "$INTAKE_CHECKOUT/examples/workbench/host.patch"
cp "$INTAKE_CHECKOUT/examples/workbench/SourceSchemaPanel.tsx" web/src/
cp "$INTAKE_CHECKOUT/examples/workbench/source-schema-panel.css" web/src/
cp "$INTAKE_CHECKOUT/examples/workbench/SourceSchemaPanel.test.tsx" web/src/
cp "$INTAKE_CHECKOUT/examples/workbench/source-schema.spec.ts" web/e2e/
cp "$INTAKE_CHECKOUT/examples/workbench/test_source_schema_catalog.py" tests/
```

Set `PDTL_SCHEMA_CATALOG_ENABLED=1` before launching the host with its normal demo command.
Use a new synthetic runtime root. The example is a local integration; it does not supply
an authenticated public deployment. The source catalog is a separate
`source-schema-catalog.sqlite3` under the host's retained root.

## Analyst journey

1. Upload a synthetic CSV with `NPI,Site,Directory Flag` and rows ending in `Y` and `N`.
2. Open **Source format catalog** in the delivery detail. Declare payer/network/guide version
   only when supplied by the source. Inspect the unknown or candidate format.
3. Bind practitioner NPI, office ID and directory visibility. Set the exact exclusion and
   display values according to the source guide; retain a supporting excerpt. Review row
   grain, date formats and any source-local requirements/conditions.
4. Preview the proposed values and findings. The literal `false` value is visible; held
   participation remains separate from format review.
5. Save the proposal, then record the reviewer and reason. This does not accept the delivery.
6. Upload a later delivery from that sender with the same scoped format. Reopen/reload;
   recognition is exact with no model call. Another sender cannot inherit the flag meaning.

An external agent can submit proposals through MCP against the same catalog and a
host-registered delivery whose `delivery_id` is `RECEIPT_ID:ATTEMPT_ID`. Pending proposals
are visible in the panel. Loading, previewing and recording the analyst's review are
separate actions; agents have no review tool.

## HTTP adapter

`provider_network_intake.workbench.mount(app, host, catalog)` lazily imports FastAPI.
The host protocol supplies retained artifact storage, a current receipt/attempt read and
a SQLite connection. The routes share `/api/intakes/{receipt_id}/source-schema`:

| POST path | Purpose |
|---|---|
| `/inspect` | Source descriptor, recognition, draft and pending proposals |
| `/preview` | Pinned preview and token binding exact input/context/catalog/values |
| `/proposals` | Save a previewed manual proposal |
| `/proposals/{id}/inspect` | Load a proposal for this exact retained attempt |
| `/proposals/{id}/review` | Explicit analyst decision with current preview/revision |

Requests name `expected_attempt`; preview/proposal/review name `expected_catalog_revision`.
Proposal/review require the current `preview_token`. Host-attempt changes and catalog
changes produce conflicts. Source-format review writes only the separate catalog. Actor
attribution and authentication belong to the host.

## Checks

With the library installed in the selected Python interpreter:

```sh
PYTHONPATH=src python -m pytest -q tests/test_source_schema_catalog.py
npm test --prefix web -- SourceSchemaPanel.test.tsx
npm run build --prefix web
PYTHONPATH="$PWD/src" PDTL_SCHEMA_CATALOG_ENABLED=1 npm run test:e2e --prefix web -- source-schema.spec.ts
```

Ensure `python3` in the browser test PATH is that interpreter. The Python integration uses
the real host services, verifies exact originals and unchanged host SQL, and reads back a
previously approved export through its independent consumer. Browser cases cover desktop
and phone layouts, separate review, later-delivery reuse and reload.
