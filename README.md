# Provider Network Intake

Interpret messy provider and insurance-network rosters, review unfamiliar source formats,
and reuse those interpretations on the next delivery.

A sender renames `Practitioner NPI` to `NPI`, `Office ID` to `Site`, and replaces a display
field with an exclusion flag. Guessing from the headers can reverse the flag or confuse
billing and rendering identifiers. This library makes that uncertainty visible, previews
an evidence-backed interpretation, and records a reviewed source variant for later reuse.

The core is Python 3.11+ with **no runtime dependencies**. It works as a library, CLI,
optional MCP server, or optional FastAPI integration. Ordinary inspection is offline.

## Try the complete offline journey

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
network-intake demo --root demo-output
```

Use a new output directory. The synthetic demo reviews a changed format, reopens the
SQLite catalog, recognizes a later delivery with the model disabled, rejects inheritance
by another sender, and exports inspectable evidence. `Y` becomes the boolean
`directory_visible=false`; it never becomes an assertion of insurance participation.

```text
Unfamiliar delivery → inspect → candidate/unknown → preview → review source variant
                                                               ↓
Later delivery → same scoped catalog → deterministic interpretation, model disabled
```

For an analyst-facing integration, see the [Workbench adapter and review panel](examples/workbench/README.md).
For a second consumer, [batch_exceptions.py](examples/batch_exceptions.py) produces CSV
exceptions and a portable evidence bundle without importing the Workbench.

## Use it from Python

```python
from provider_network_intake import CatalogSnapshot, Engine, SourceContext

engine = Engine()
context = SourceContext(source="synthetic-sender", delivery_id="delivery-1")
payload = b"Practitioner NPI,Office ID\n1000000004,NORTH\n"
inspection = engine.inspect(payload, context=context)
catalog = CatalogSnapshot(revision=0, variants=())
recognition = engine.recognize(inspection, catalog=catalog)
proposal = engine.propose_adaptation(inspection, recognition=recognition, catalog=catalog)
report = engine.preview(payload, proposal=proposal, context=context)
assert report["rows"][0]["normalized"]["location_id"] == "NORTH"
assert report["eligibility"]["state"] == "held"
```

A held report still supplies useful findings and lineage. Reviewing a source format and
accepting a dataset are separate decisions. The library never approves operational data.

## Scope

| Input | Supported behavior |
|---|---|
| CSV clinic/payer rosters | Text-faithful fields, explicit mappings and value/date transforms, source-local requirements, reviewed variants |
| Saved NPPES API 2.1 JSON | Provider/reference facts with all addresses retained; no network-participation assertion |
| Saved FHIR R4 / Plan-Net 1.2 searchset | Bounded Practitioner, Organization, Location and PractitionerRole interpretation; conservative relationships and explicit unsupported coverage |

Variants are scoped to sender, payer, network and declared source-guide version. Conflicting
meanings remain ambiguous. Changed guide versions require review. The catalog retains
immutable definitions, supporting excerpts, proposals and append-only review history.
Source observations do not redefine official standards or consumer policy.

Optional AI assistance proposes a match, a bounded new interpretation, or abstention.
It uses the same preview contract as manual review. API calls require explicit opt-in;
MCP can save proposals but cannot review or accept them. Known formats work without AI.

## Evidence and limits

Previews retain bounded detail while assessing every supported row. Full exports stream
all row/field lineage to JSONL with SHA-256 manifests; imports carry no approval authority.
CSV input is capped at 10 MiB, 10,000 rows and 64 columns. Native JSON is bounded and parsed
in memory. This is a small-batch interpreter, not a national directory crawler or a full
FHIR validator. See [limits and validation](docs/LIMITS.md).

The [frozen synthetic evaluation](docs/VERIFICATION.md) tests recognition, semantic errors,
scripted review corrections and counterexamples. Actual-model benefit and analyst time
savings have not been established. All committed records are synthetic.

## Documentation

- [API and CLI](docs/API.md): persistence, proposals, exports and error states
- [AI and MCP](docs/AI.md): configuration, receipts, resource bounds and authority
- [Architecture](docs/ARCHITECTURE.md): module boundaries and design decisions
- [Verification](docs/VERIFICATION.md): reproducible checks and measured scope
- [Sources and reuse](docs/SOURCES.md): standards, source semantics and design references

## Development

```sh
python -m pip install -e '.[dev,mcp,workbench]'
python -m pytest -q
python -m mypy src
ruff check src tests scripts examples/batch_exceptions.py
ruff format --check src tests scripts examples/batch_exceptions.py
python -m build
```

MIT licensed. This package is an early local toolkit; broader payer submission, terminology,
credentialing, identity resolution and production compliance require separate work.
