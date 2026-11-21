# Sources and reuse

The implementation and synthetic corpus are original. The following projects informed the
boundaries and evaluation; they are design references, not bundled dependencies or claims
of a new schema-matching invention.

| Source | Relevant pattern |
|---|---|
| [Provider Directory Workbench](https://github.com/mlow-io/provider-directory-workbench/tree/52a6f4f) | Original application context: retained originals, data-only normalization recipes, separate review/acceptance, native source evidence |
| [Frictionless](https://github.com/frictionlessdata/frictionless-py) | Consumable tabular descriptors and row/field diagnostic reports |
| [CanDIG clinical ETL](https://github.com/CanDIG/clinical_ETL_code/tree/2c5cbe80e3642c094a7d1aa6d063787b9db8631a) | Explicit mapping configuration, coverage statistics and separate conversion diagnostics |
| [FHIR Schema](https://github.com/fhir-schema/fhir-schema/tree/3f5c3eda5e919de37e58a93291889c508abc933b) | Explicit schema/terminology boundaries and path-qualified errors |
| [Valentine](https://github.com/delftdata/valentine) | Schema-matching evaluation against ground truth and candidate outcomes |
| [HL7 Java validator](https://github.com/hapifhir/org.hl7.fhir.core) | External standards engine and retained OperationOutcome reports |

No third-party implementation or payer template is copied into the library. CanDIG's
LGPL-licensed code is a reference only. External validators and their package caches are
caller-installed and outside the distribution. No HL7 package archives, payer records or
terminology datasets are redistributed.

## Standards and source semantics

The native target is deliberately pinned to [FHIR R4](https://hl7.org/fhir/R4/) and
[Plan-Net 1.2.0](https://hl7.org/fhir/us/davinci-pdex-plan-net/STU1.2/), whose subject is
payer plans, networks and participating providers. This is a declared consumer subset,
not complete guide conformance. Saved NPPES inputs follow the [NPI Registry API 2.1](https://npiregistry.cms.hhs.gov/api-page).

The fabricated roster cases exercise distinctions documented by primary sources:
[Sunshine provider support](https://www.sunshinehealth.com/providers/support.html) describes
roster/enrollment submission;
[Oscar field definitions](https://assets.ctfassets.net/plyq12u1bv8a/6Vd691XWZjaBOnJVgDMbRn/4a98994186a514c854f2b55f32881e4a/Professional_Standard_Roster_Template_v._1.0_-_Field_Definitions.pdf)
distinguish identifier roles, office grain, conditional fields and termination scope; and
[Devoted provider-data instructions](https://www.devoted.com/providers/providers-data-cred/)
provide directory-exclusion and roster-change context. These sources motivate synthetic
semantics; the corpus is not an actual payer delivery or a complete submission validator.

The contribution tested here is combining scoped recognition, reviewed semantic variants,
partial-validation diagnosis, reusable evidence and optional agent proposals in one narrow
analyst task. The references do not establish research novelty or market uniqueness.
