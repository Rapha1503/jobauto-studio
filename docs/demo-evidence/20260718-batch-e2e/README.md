# Batch E2E evidence - 2026-07-18

This proof uses only the synthetic `Alex Morgan` profile and a local tracker.
It does not contain a real candidate identity or claim that a live application was submitted.

## Proven path

```text
Codex Web discovery
  -> 7 official offer candidates
  -> deterministic eligibility and ranking
  -> replacements after terminal fit gaps
  -> 3 independently generated application packages
  -> ATS and editorial supervision
  -> real one-page CV and letter PDFs
  -> SHA-256 artifact verification
  -> 3 Chrome-ready handoffs
  -> 3 deterministic sandbox confirmations
```

Discovery used one `gpt-5.6-sol` call, returned seven official URLs and took
109.3 seconds. The campaign initially selected three offers. Two were stopped
before writing because their mandatory three-year experience requirement was
not supported. A third package was rejected by the supervisor. The campaign
then promoted three eligible replacements and completed all three.

## Approved packages

| Company | Role | Campaign score | Final score | ATS | Calls | Est. tokens | Evidence |
|---|---|---:|---:|---:|---:|---:|---|
| SFEIR | Cloud & Data Engineer | 71 | 93 | 97 | 8 | 74,864 | [CV](sfeir-cloud-cv.pdf), [letter](sfeir-cloud-letter.pdf) |
| SFEIR | Data Engineer - augmented expert | 65 | 92 | 94 | 6 | 63,910 | [CV](sfeir-augmented-cv.pdf), [letter](sfeir-augmented-letter.pdf) |
| Expleo | Data Engineer | 74 | 91 | 92 | 8 | 81,836 | [CV](expleo-cv.pdf), [letter](expleo-letter.pdf) |

All six PDFs contain one page. Their hashes are recorded in
[`batch-summary.json`](batch-summary.json). The PNG files in this directory
are page-one renders used for visual inspection.

## Adaptation evidence

- **SFEIR Cloud & Data Engineer:** headline and summary emphasize BigQuery,
  dbt, cloud pipelines and data observability. The data-quality project is
  ranked first. Cloud Composer is labelled transferable from verified Airflow
  experience rather than presented as a completed fact.
- **SFEIR augmented Data Engineer:** headline and summary emphasize Spark,
  Kafka, GCP, distributed batch/streaming workflows and governance. The
  real-time mobility platform is ranked first.
- **Expleo Data Engineer:** headline and summary emphasize Python, SQL,
  ETL/ELT, governed datasets and stakeholder-facing analytics. The letter
  connects those verified capabilities to the aerospace context without
  inventing aerospace experience.

## Handoff evidence

Each approved package produced a `ready_for_chrome` handoff. The sandbox then
uploaded the exact approved CV and letter bytes, rechecked both SHA-256 hashes
and returned `sandbox_verified`:

- `handoff-e496047655e24b13`
- `handoff-4a0df0f077464015`
- `handoff-9f320a25415c4707`

`sandbox_verified` deliberately does not update the tracker as `submitted`.
Live career-site submission through the Codex Chrome Extension remains a
separate acceptance gate. Before that gate, the demo needs a dedicated test
Gmail identity so no real candidate profile is affected.

## Failures that were not hidden

- SFEIR Data Engineer GCP and Infinite Lambda were stopped on explicit
  mandatory experience gaps.
- Vente-unique reached review but was falsely rejected because the supervisor
  view omitted the locked baseline CV. The generic context-boundary fix is
  covered by targeted tests.
- A fresh Vente-unique replay then stopped earlier on an unsupported mandatory
  Snowflake requirement. This is truthful, but it also shows that brief
  classification is not perfectly stable between model calls.
- The discovery result lacked company diversity: four of seven candidates were
  SFEIR roles. Diversity constraints and duplicate-company ranking remain a
  product-quality improvement, not a hidden success claim.

