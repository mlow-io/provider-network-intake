# Provider Network Intake

**Deterministic, evidence-preserving healthcare roster ingestion & schema adaptation — with optional air-gapped or MCP AI assistance.**

Healthcare payers and medical groups exchange provider rosters under strict **No Surprises Act** directory timelines (90-day verification, 2-business-day updates), yet real-world spreadsheets silently rename columns and invert meanings—turning `Directory_Flag = Y` from *display* into *exclude*, or mixing billing and rendering NPIs. Guessing headers corrupts directories and risks ghost networks.

`provider-network-intake` lets an analyst (or an AI agent proposing via MCP/LLM) inspect unfamiliar source formats **once**, record a reviewed, data-only variant in SQLite, and execute every future delivery **deterministically in ~0.5 seconds (10,000 rows, 96 MiB peak RSS) with zero model calls**.

Built as the embeddable ingestion core for [**Provider Data Workbench**](https://github.com/mlow-io/provider-data-workbench), with **zero runtime dependencies** in Python 3.11+.

```text
Unfamiliar roster ──▶ inspect (shape + counts only, no raw row PHI sent to models)
                          │
        ┌─────────────────┴─────────────────┐
        ▼                                   ▼
Known variant in SQLite              Unfamiliar or drifted schema
(~0.5s / 10k rows, AI off)           (AI/MCP proposes mapping; CANNOT approve)
        │                                   │
        └─────────────────▶ preview ◀───────┘
                              │
                   Human review & sign-off
                              │
                              ▼
         Markdown drift report · JSONL/SHA-256 evidence bundle
```

## Key capabilities

- **Healthcare-native validation:** CMS ISO 7812 Luhn check-digit verification (`80840` prefix) on `practitioner_npi`, `billing_npi`, and `group_npi` (`is_valid_npi`), US state/ZIP/phone/date normalization, conditional rules (`required_when`), and explicit `directory_visible` boolean polarity tracking (`Y` → `false`, never inventing insurance participation).
- **Source-scoped variant catalog:** Variants isolate by `(sender, payer, network, source_version)` in an append-only SQLite store with optimistic concurrency and immutable revision history—another payer never silently inherits an incompatible flag meaning.
- **Pluggable AI & local air-gapped LLMs:** Supports OpenAI Responses (`ResponsesProvider`), local/self-hosted Ollama & vLLM (`OpenAICompatibleProvider` over `http://127.0.0.1`), custom package `entry_points` (`provider_network_intake.assistants`), and a 7-tool stdio **MCP server** that can inspect descriptors and submit proposals **but has no review or approval tool**.
- **Bounded native FHIR & NPPES readers:** Parses saved NPPES API 2.1 JSON and FHIR R4 / Plan-Net 1.2 searchset Bundles conservatively while preserving unsupported coverage and unresolved references.
- **Portable audit outputs:** Generates Markdown quality/drift reports (`network-intake report`), spreadsheet-safe CSV exceptions (`exceptions.csv`), and streaming JSONL evidence bundles with SHA-256 completion manifests.

## Quickstart (60-second offline demo)

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install .
network-intake demo --root demo-output
```

The offline synthetic demo inspects an unfamiliar roster, records a reviewed source variant in SQLite, reopens the catalog to recognize a follow-up delivery deterministically with the model disabled, blocks cross-sender inheritance, and verifies a SHA-256 evidence bundle.

Run a Markdown drift & quality report on any CSV:

```sh
network-intake report roster.csv --source clinic-a --delivery nov-2026 --output report.md
```

## Python API

```python
from provider_network_intake import CatalogSnapshot, Engine, SourceContext, is_valid_npi

assert is_valid_npi("1990001012") is True

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

Reviewing a source schema and approving operational provider data stay strictly separated—the library never auto-approves directories.

## Supported sources & measured performance

| Input | Supported behavior | Benchmark (M4 arm64, Python 3.13) |
|---|---|---|
| CSV clinic / payer rosters | Explicit delimiter/encoding, allowlisted transforms, value/date maps, conditional requirements, Luhn NPI check | **0.494 s** warm median · **96.4 MiB** RSS (10,000 rows × 64 cols, 1,000 catalog variants) |
| Saved NPPES API 2.1 JSON | Provider & multi-address reference retention, Luhn NPI check; never invents participation | Bounded 10 MiB / 100k JSON nodes |
| Saved FHIR R4 / Plan-Net 1.2 | Conservative `Practitioner`, `Organization`, `Location`, `PractitionerRole` graph & locators | **0.058 s** warm median · **60.5 MiB** RSS (1,000 entries) |

See [Verification](docs/VERIFICATION.md) and [Limits](docs/LIMITS.md) for bounds, synthetic evaluation methodology (38 fixed-heuristic errors eliminated across 6 drift families after 1-time review), and non-goals.

## Documentation & integrations

- [**Provider Data Workbench (UI Host)**](https://github.com/mlow-io/provider-data-workbench) — Full-stack FastAPI + React/shadcn operator console embedding this package
- [API & CLI Reference](docs/API.md) — Python API, CLI commands, and canonical target fields
- [AI, Local LLMs & MCP](docs/AI.md) — Ollama/OpenAI-compatible setup, entry-point plugins, and 7-tool stdio MCP server
- [Architecture](docs/ARCHITECTURE.md) — Module boundaries, SQLite locking, and streaming guarantees
- [Verification](docs/VERIFICATION.md) · [Limits](docs/LIMITS.md) · [Sources](docs/SOURCES.md)
- [Host Integration Example](examples/workbench/README.md) · [Standalone Batch Consumer](examples/batch_exceptions.py)

## Development

```sh
python -m pip install -e '.[dev,mcp,workbench]'
python -m pytest -q
python -m mypy src
ruff check src tests scripts examples/batch_exceptions.py
ruff format --check src tests scripts examples/batch_exceptions.py
python -m build
```

MIT licensed. This toolkit handles small-batch local roster interpretation and schema governance; full credentialing, payer submission, and multi-tenant auth belong in the host system.
