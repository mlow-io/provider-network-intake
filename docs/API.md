# API and CLI

## Interpretation and review

`Engine.inspect(payload, context=..., format_hint='csv', settings=...)` returns a source
shape descriptor with aggregate counts, source fields, settings and input digest. CSV
settings accept explicit `delimiter` and `encoding`; there is no dialect guessing.

`Engine.recognize(inspection, catalog=snapshot)` returns `exact`,
`compatible_with_drift`, `ambiguous` or `unknown`. A guide-version change is a candidate
reason, not automatic selection. Inspection/recognition never writes a catalog.

`Engine.propose_adaptation(...)` provides a deterministic draft or a reviewed candidate.
`Engine.proposal(inspection, variant, catalog_revision=...)` pins a caller-authored
`Variant.from_dict(...)` to input/context/catalog. `Engine.preview(...)` rejects changed
bytes, context, definition digests and expected catalog revision. Reports include original
values, normalized values, locators, transformations, coverage, all finding counts, bounded
finding samples, separate validation layers and eligibility. `detail_limit=0` still assesses
all rows. `preview_native` handles saved `nppes` and `plannet` JSON.

```python
from provider_network_intake import CatalogStore, Variant

# inspection and definition come from the current source and a reviewed source guide.
store = CatalogStore("source-catalog.sqlite3")
snapshot = store.snapshot(inspection.context.source)
variant = Variant.from_dict(definition)
proposal = store.propose(inspection, variant, origin="manual",
                         request_id="review-delivery-b", expected_revision=snapshot.revision)
report = engine.preview(payload, proposal=proposal, context=inspection.context,
                        expected_catalog_revision=snapshot.revision)
# A human inspects the preview before recording this explicit decision.
event = store.review(proposal["id"], actor="Reviewer", reason="Checked source instructions",
                     expected_revision=snapshot.revision)
```

This records a source-format interpretation even if the delivery is held. It does not accept
rows. The caller supplies trustworthy actor identity; this local library has no authentication
service. `review(..., supersedes=old_variant_id)` explicitly replaces an obsolete interpretation
within the same sender/payer/network/contract. History remains intact. `snapshot`, `variant`,
`proposal`, `pending` and paginated `history` support readback.

## Variant example

```json
{
  "version": 1,
  "source": "synthetic-sender",
  "contract": "payer-network-roster/1",
  "grain": "practitioner_office",
  "headers": ["NPI", "Site", "Directory Flag"],
  "mapping": {"practitioner_npi": "NPI", "location_id": "Site", "directory_visible": "Directory Flag"},
  "transforms": {"directory_visible": [{"op": "value_map", "values": {"Y": false, "N": true}}]},
  "evidence_refs": ["synthetic-guide"],
  "source_notes": {"synthetic-guide": "Original synthetic instruction: NPI is practitioner, Site office key; Y excludes and N displays."}
}
```

Optional scope is `payer`, `network`, `source_version`. Local `required` fields add to the
base contract. `required_when` is a list of `{field, equals, require}` rules over mapped
classifiers. Blank classifiers produce `condition_unassessed`. `ignored_fields` explicitly
names source headers omitted from the target, with evidence; uncovered columns remain visible
and block complete coverage. Source-local rules never remove base contract requirements.
`SourceContext.completeness` is a caller declaration (`unknown`, `partial`, `full`, `delta`),
not proof that missing records are terminated.

## Portable output

`exports.write_full_bundle(new_directory, payload, context=..., proposal=...,
include_original=False)` assesses the entire input and streams every supported source row's
lineage. For native input use `format_hint`. The completion manifest covers `report.json`,
`variants.json`, `history.jsonl`, `evidence.jsonl` and optional `original.bin`. Preview rows may
be truncated while `evidence_complete=true` covers all rows. `write_bundle` can export an
explicitly bounded report without claiming full evidence. History is caller-selected, not
implicitly the whole catalog.

`read_bundle` verifies integrity and returns `import_state='proposal_only'`.
`iter_bundle_evidence` yields verified source evidence; consume the iterator fully before
trusting its final count/hash checks. `exceptions_csv(report)` produces a spreadsheet-safe
projection of the report's sampled findings; aggregate counts remain in JSON. Original bytes
are opt-in because exported data can be sensitive.

`validation.attach_validation(report, payload, outcome_bytes, manifest)` attaches an external
FHIR OperationOutcome with input/report/binary/package digests, versions, arguments, scope,
terminology mode and declared outcomes. Attribution is `caller_supplied_invocation`; the
function does not independently run or authenticate a validator. Disabled terminology and
selected-resource scope cannot establish a whole-artifact pass. Original findings and host
approval remain unchanged.

## Commands

```sh
network-intake contracts
network-intake inspect roster.csv --source sender --delivery b
network-intake recognize roster.csv --source sender --delivery b --catalog catalog.sqlite3
network-intake propose roster.csv --source sender --delivery b --catalog catalog.sqlite3 \
  --definition definition.json --save --request-id review-b --output proposal.json
network-intake preview roster.csv --source sender --delivery b --catalog catalog.sqlite3 \
  --proposal proposal.json --output preview.json
network-intake report roster.csv --source sender --delivery b --catalog catalog.sqlite3 \
  --output report.md
network-intake review --catalog catalog.sqlite3 --proposal-id PROPOSAL_ID \
  --actor Reviewer --reason 'Checked source instructions' --expected-revision 0
network-intake export roster.csv --source sender --delivery b --catalog catalog.sqlite3 \
  --definition definition.json --bundle evidence-output
network-intake import evidence-output
```

Use `--format nppes` or `--format plannet` for native inspection/preview/export. Scope flags
must match the definition. Output files and bundle directories refuse overwrite. Exit codes:
`0` operation completed, `2` held interpretation or requested model evaluation unavailable,
`1` invocation/input failure. A held export is still created and inspectable. CLI help is the
complete option reference. Public wire versions are `1`; incompatible major versions fail.

`IntakeError` means unsupported/invalid input, `ResourceLimit` exceeds a declared bound, and
`Conflict` means stale/current-state disagreement. Both specialize `IntakeError`. Library
callers handle filesystem errors when they own paths. The optional HTTP mount maps invalid
requests to 422, stale state to 409 and missing host receipts to 404.

## Embedding in another UI or service

Treat the engine as an application service: pass bounded saved bytes and explicit source
context, then serialize `inspection.to_dict()`, recognition and preview dictionaries to your
frontend. Keep the catalog path and review identity on the trusted application side. The
engine does not import Workbench, FastAPI, a model SDK or a global configuration file.

For a stateless integration, supply `CatalogSnapshot` directly. For local persistence use
`CatalogStore`. For a custom review UI, display the pinned proposal/preview, then call
`store.review` only after the separate user action. Keep your dataset-approval workflow in
your own application. If requests are concurrent, use the current revision and input digest;
return a stale-state conflict rather than silently rebuilding and approving a changed input.

The [optional FastAPI/React example](../examples/workbench/README.md) demonstrates one host
integration. It is not required for the CLI, MCP server, Python API or standalone batch
consumer. Public entry points are importable functions and classes; applications need not
spawn the CLI or parse terminal text to use the library.

## Canonical tabular fields

`provider_network_intake.contracts.FIELDS` is the complete allowlist. Field names are stable
within wire version 1; unsupported targets fail validation rather than disappearing.

| Meaning | Fields |
|---|---|
| Source record/change | `record_id`, `record_action`, `entity_class` |
| Identifier roles | `practitioner_npi`, `billing_npi`, `group_npi` |
| Provider/office | `provider_name`, `location_id`, `address`, `address_line2`, `city`, `state`, `zip` |
| Contact | `phone`, `phone_extension`, `fax` |
| Directory/panel | `directory_visible`, `panel_status` |
| Declared insurance scope | `payer_id`, `network_id`, `plan_id`, `reported_status` |
| Separate calendar dates | `effective_start`, `effective_end`, `office_end`, `credentialing_end`, `source_updated_at` |

Targets retain text, except the explicitly interpreted `directory_visible` boolean. Date
operations produce ISO calendar dates; timestamp parsing and terminology validation are not
implied. NPI, ZIP and extension values remain text; identifier fields (`practitioner_npi`,
`billing_npi`, `group_npi`) validate both 10-digit format and the CMS ISO 7812 Luhn check
digit (`is_valid_npi`). `reported_status` is a source assertion;
`record_action` is a delivery/change instruction and never substitutes for participation.
