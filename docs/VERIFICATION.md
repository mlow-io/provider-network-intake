# Verification

All committed input examples are original synthetic records. These checks establish bounded
software behavior, not real payer accuracy, analyst time savings or clinical suitability.

## Reproduce

```sh
python -m pytest -q
python -m mypy src
ruff check src tests scripts examples/batch_exceptions.py
ruff format --check src tests scripts examples/batch_exceptions.py
python -m provider_network_intake.benchmark
python scripts/check_lifecycle.py
network-intake evaluate --root new-evaluation-output
```

The package suite covers scope/meaning preservation, malformed inputs, resource limits,
truncation with complete aggregate blockers, immutable/stale/concurrent reviews, restart,
controlled API failures/refusals/unknown outcomes, cancellation, real MCP stdio transport,
export round trips/tampering, validator attribution and optional host integration. Local
qualification passed 115 tests, strict typing and Ruff. Optional tests use MCP SDK 1.14.0
and FastAPI; base-package installation does not require either.

## Recognition and adaptation

The [frozen corpus](../src/provider_network_intake/assets/adaptation-manifest-v1.json) has
six related A/B/C/D families, 24 deliveries. Cases exercise exclusion polarity, billing vs
rendering IDs, office vs credentialing dates, a conditional group requirement, explicit delta
actions and a changed declared guide version. They are purpose-written engineering cases,
not a representative sample of real rosters.

B is scored with only A reviewed. Gold B review is then scripted and retained, followed by
C reuse and D counterexamples with the model disabled. Expected normalized values and
gold definitions are excluded from cold model requests. [Full outcomes](evidence/adaptation.json)
retain each deterministic proposal, rule/binding/value errors and counterexample result.

| Repeated C delivery | Fixed heuristics | Reviewed catalog |
|---|---:|---:|
| Script-scored review corrections | 38 | 0 |
| Reviewed-catalog wrong supplied semantic values | — | 0 |
| Later scoped exact recognition | — | 6/6 |
| D counterexamples correctly matched/held/abstained as specified | — | 6/6 |

Correction counts are field/rule differences, not observed human effort. This demonstrates
reuse after a supplied correct review. It does not establish autonomous discovery quality.
No real model calls were made: configured model access was unavailable. Controlled-provider
checks do not establish an AI advantage. Actual-model and uncoached-analyst usefulness
remain separate open evidence questions; ordinary deterministic use is complete without them.

## Runtime and memory

[Measurements](evidence/benchmark.json) use CPython 3.13.2 on Apple M4 / macOS arm64.
Each format runs in a fresh subprocess: one cold and five warm executions. Latency includes
inspection, scoped recognition where applicable, preview and bounded report construction.
Peak RSS is total process high-water memory, including input and catalog.

| Fixture | Cold | Warm median | Maximum | Peak RSS |
|---|---:|---:|---:|---:|
| CSV: 10,000 rows, 64 columns, 9,171,002 bytes, 1,000 variants | 0.503 s | 0.494 s | 0.503 s | 96.4 MiB |
| FHIR: 1,000 entries, 9,177,683 bytes, native graph/no variant catalog | 0.058 s | 0.058 s | 0.059 s | 60.5 MiB |

Both meet the declared fixture targets of 5 seconds and 256 MiB. These are local observations,
not arbitrary-workload guarantees. Native recognition does not use tabular catalog variants.
The timing excludes external models, Java validation and full-evidence export. CSV processing
has an additional cumulative transform-work budget for adversarial repeated operations.

A [separate tracing run](evidence/lifecycle.json) performs five warmups and 20 repeated
500-row interpretations, full streamed-bundle verification and SQLite reopen/read cycles.
Live traced allocations grew 153,130 bytes after warmup; open handles remained 5 → 5.
Traced peak allocations were 3,571,281 bytes. The predeclared retained-growth/handle gates
were 1 MiB / two handles. Tracing overhead is excluded from latency benchmarks. This is
bounded retained-object evidence; allocator RSS high-water alone is not treated as a leak.

## Host and distribution

The optional Workbench adapter is verified in an isolated checkout of public base `52a6f4f`.
The original host checkout remains unchanged. Python integration checks use actual retained
artifacts/current attempts and a real approved managed-feed export; format review changes
neither host SQL nor the independent consumer's pinned bytes/contacts.

The installed-library qualification recorded:

| Check | Observed result |
|---|---|
| Library from source and installed wheel | 115 tests passed; strict Mypy and Ruff passed |
| Real Workbench backend | 873 tests and synthetic pipeline passed |
| Frontend | 136 tests and production build passed |
| Desktop/phone browser coverage | 74 scenarios passed across the full run and a targeted environment retry |
| Source-format journeys | Both desktop and phone cases passed; panel screenshots visually inspected |
| Standalone CLI | Eleven installed-command operations passed, including proposal/review, later recognition and full export/import |
| Wheel and sdist | Fresh isolated installs outside checkout ran demo, frozen evaluation and independent batch/evidence readback |

The final full browser run passed 73 scenarios; one phone screenshot failed with `ENOSPC`.
After reclaiming task-generated successful screenshots, that exact scenario passed in a
fresh targeted run. Earlier environment failures involved a stale installed host CLI and
disk-exhausted SQLite fixture storage. Logs and failed fixtures were preserved separately;
none of those failed attempts is counted as a passing run. Successful backend fixture
retention was reduced for the final full run. The inherited Vite chunk-size advisory was
nonfatal. No canonical host data was changed.

The installed-package suite includes real MCP stdio transport and the optional HTTP bridge.
The base wheel imported no optional frameworks. Distribution archives were inspected for
packaged assets/examples and excluded private/runtime/image files. CI defines Python
3.11–3.13 on Linux and macOS, package construction and installed-wheel demo/evaluation.
Its live job results are separate from these local receipts.

## External validation

Ordinary parsing reports profile/terminology `not_run`. The attachment boundary was also
exercised with [a saved real HL7 Java validator 7.0.5 report](evidence/saved-validator-attachment.json)
from the original Workbench's synthetic Bundle. Input/report/JAR digests were verified against
its retained manifest. The output has four errors, eleven warnings and two informational
issues; attached profile is `failed`, terminology `incomplete`, eligibility `held`.
Package hashes identify metadata only. This was attachment of an existing invocation, not a
new Java run, and demonstrates retention of a standards failure rather than conformance.
Caller-attributed manifests are not independent attestations of tool execution.
