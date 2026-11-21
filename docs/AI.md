# Optional AI assistance

Known-variant recognition and interpretation require no model. AI is an optional collaborator
for unfamiliar fields and source semantics. Assistance is experimental; controlled tests
establish engineering behavior, not the quality of an actual model's suggestions.

## Direct API route

The included `ResponsesProvider` uses one explicitly authorized OpenAI Responses request
with a strict structured-output schema. Supply an account key through `OPENAI_API_KEY` and
choose a model supporting that endpoint/schema. There is no default model, discovery of
other applications' credentials, automatic invocation or automatic retry.

```sh
network-intake assist roster.csv --source sender --delivery b --catalog catalog.sqlite3 \
  --evidence curated-excerpts.json --model "$PNI_MODEL" --allow-external-processing \
  --journal invocations.sqlite3 --request-id delivery-b-attempt-1 --output suggestion.json
network-intake preview roster.csv --source sender --delivery b --catalog catalog.sqlite3 \
  --proposal suggestion.json
```

The evidence file is a bounded JSON object mapping caller-curated IDs to source-guide
excerpts. Review what will be sent: descriptors include header text and aggregate counts;
curated evidence and reviewed definitions can also contain sensitive information. Original
row values are omitted from the API context. `store=false` is requested; caller/provider
retention arrangements remain outside this library.

The result is a match, bounded new variant, or abstention with evidence references and
unresolved questions. Operation allowlists, field/source checks, scope and revision pins,
and preview validation still apply. Excerpts are retained with proposed definitions. A
conflicting current excerpt cannot silently override a reviewed interpretation.

Receipts retain provider/model, prompt version, request/context hashes, supplied context,
raw response bytes/digest, latency and available token usage. Refused, malformed, oversized
and late responses remain failed evidence. An oversized response retains a bounded prefix
with `response_complete=false`. Keys never enter receipts. Journal claims are atomic; the
same ID replays a completed local result. A running/unknown outcome needs reconciliation
with a new deliberate action, not an automatic remote retry. Cancellation prevents late
proposal publication. This is not a transactional exactly-once remote API guarantee.

The endpoint is fixed HTTPS, redirects rejected, context/request/response/output/deadline
bounds enforced. HTTP failures and invalid responses never become usable proposals. See
[resource limits](LIMITS.md). No source text receives execution or approval authority.

## MCP route

```sh
python -m pip install '.[mcp]'
network-intake-mcp --session session.json --catalog catalog.sqlite3
```

The caller owns a session manifest and registers explicit local artifacts:

```json
{"artifacts":[{"id":"roster-b","path":"roster.csv","format":"csv",
"context":{"source":"synthetic-sender","delivery_id":"b"},
"evidence":{"synthetic-guide":"Original synthetic instruction: Y excludes this office."}}]}
```

An MCP client launches this stdio command. The external agent receives artifact IDs rather
than arbitrary filesystem/URL access. Seven tools expose the common boundary:

| Tool | Effect |
|---|---|
| `list_contracts` | Formats, capabilities and registered artifact IDs |
| `inspect_delivery` | Shape/counts and curated source excerpts |
| `match_variants` | Scoped recognition candidates |
| `get_variant` | A reviewed definition in the artifact's declared scope |
| `preview_adaptation` | Bounded normalized values, lineage and findings |
| `get_findings` | Aggregate coverage/layers/blockers |
| `submit_adaptation_proposal` | Idempotent agent-attributed proposal in the local catalog |

There is no review, dataset-acceptance or external-fetch tool. The host/analyst reviews a
saved proposal separately. MCP previews can expose source row values to the connected
client; unlike the direct API's descriptor-only context, this transport is a host-controlled
interactive evidence surface. Register only authorized data.

`create_server(deliveries, catalog)` supports embedded callers with `Delivery` objects.
The SDK is imported only when this optional server is created. The base package remains
independent of any model SDK, MCP SDK, HTTP framework or Workbench database.

## Evaluation

`network-intake evaluate --root evaluation-output` runs the frozen synthetic corpus offline.
For an explicitly requested real-provider comparison, add `--invoke-model --model "$PNI_MODEL"`
with a supplied key. It makes at most six calls, one cold proposal per family, with the same
schema/output/deadline bounds. Preserve each new output directory. Gold definitions and
expected normalized values are excluded from cold requests. Reuse and counterexample
stages run with the model disabled. An unavailable requested model condition exits 2; it
must not be reported as a model-quality pass. See [verification](VERIFICATION.md).
