# Limits and validation coverage

| Resource | Bound |
|---|---|
| Original input | 10 MiB |
| CSV | 10,000 data rows; 64 columns; 256-character headers; 8,192-character cells |
| Explicit CSV encodings | UTF-8, UTF-8 with BOM, CP1252 |
| Explicit delimiters | comma, semicolon, tab, vertical bar |
| Native JSON | depth 64, 100,000 nodes, 1,000 FHIR entries |
| Tabular transforms | 16 per mapped field; 100 million cumulative character operations |
| Preview | default 100 rows / 200 finding details; at most 1,000 each; CSV retained detail estimate 4 MiB |
| Catalog | 1,000 active variants per selected snapshot; 100,000 proposals and observations per file |
| Source evidence | at most 10 retained excerpts of 1,500 characters each |
| Export | 32 MiB metadata; 512 MiB total streamed bundle; 16 MiB per JSONL record |
| Assistance | 32 KiB context; 64 KiB request; 128 KiB response; 2,048 output tokens; 1–30 second acceptance deadline |
| MCP session | 8 host-registered artifacts / 40 MiB total; 128 KiB tool result |

All values are explicit small-batch limits. Ordinary CSV previews avoid retaining all output
rows, but input bytes and decoded text are in memory. Native JSON is an in-memory graph.
Full native exports may materialize all bounded native records. A network read may occupy
up to an additional configured socket timeout before a late response is rejected; the
acceptance deadline is not an operating-system hard kill. No implicit retries occur.

Reports distinguish decode, structure, source-contract, relationships, profile, terminology
and policy. `passed`, `failed`, `incomplete`, `not_run`, `unavailable` and `not_applicable`
have distinct meanings. Ordinary native interpretation does not execute a FHIR validator.

The FHIR subset requires a searchset Bundle. Practitioner, Organization, Location and
PractitionerRole are interpreted conservatively. Other resources, unsupported extensions,
unknown modifiers, unresolved/external references and multi-location/network ambiguity
remain retained with coverage/findings. There is no cross-product projection and no HTTP
reference resolution. A network and an insurance plan are different entities.

NPPES addresses remain reference addresses; they are not newly invented office identities.
NPI checks are ten-digit shape checks, not checksums, registry existence, licensure or
credentialing. Directory display/exclusion, office departure and credentialing termination
keep separate fields. Missing rows in partial or delta deliveries never prove termination.
Source-reported participation is not independently verified participation.

The toolkit reads saved files. It does not provide workbook/PDF/OCR parsing, payer submission,
FHIR writeback, claims/eligibility decisions, network adequacy, automatic entity merging,
patient-data processing, an authenticated multi-user service or regulatory certification.
