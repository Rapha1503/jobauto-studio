# Atomic end-to-end evidence

This folder documents one continuous public-profile run executed with the
repository code and `gpt-5.6-sol`. It is intentionally separate from older
mixed-run screenshots.

## Proven path

1. JobAuto loaded the synthetic Alex Morgan profile.
2. Codex web discovery returned five current official job links.
3. The campaign selected Pigment's Data Engineer role and launched one
   candidate-scoped application run.
4. The first rendered-document review rejected an unsupported claim about a
   completed LLM evaluation pipeline.
5. The repair pass removed that claim and regenerated both documents.
6. The final supervisor approved the package at 91/100, with ATS 93,
   editorial 94 and adaptation 91.
7. The final CV and letter were compiled, parsed, hashed and confirmed as
   one-page PDFs.
8. JobAuto created an exact Chrome handoff and the deterministic local sandbox
   accepted the two approved PDF hashes and produced a `sandbox_verified`
   receipt without marking the tracker as submitted.

The external employer submission boundary is not hidden: the sandbox proves
the form/upload/receipt contract. A real third-party submission still requires
Codex to execute the handoff in the user's authenticated Chrome Extension
session.

## Trace identifiers

- discovery: `alex-morgan-6158bd194e2e`
- campaign: `alex-morgan-58156fc74af2`
- application run: `alex-morgan-03858495e568`
- handoff: `handoff-999bf0511b2e4077`

## Artifacts

- `01-home.png`: local product entry point.
- `03-discovery.png`: native Codex web discovery with five candidates found.
- `05-run-review.png`: terminal agent trace and final supervisor scores.
- `06-tailored-cv.png`: final rendered one-page CV.
- `07-tailored-letter.png`: final rendered one-page cover letter.
- `08-handoff.png`: rehashed PDF packet and candidate submission policy.
- `09-confirmation.png`: deterministic sandbox receipt explicitly stating
  that no employer application was submitted.
- `run-summary.json`: compact machine-readable proof without local paths.

The original campaign screenshot is excluded because it exposed a parsing
defect discovered during this run: `Bac+5` was interpreted as five years of
experience. The parser now requires an explicit year unit and has regression
tests for degree labels, ranges and French/English experience wording.

## Cost and quality signal

The document run used 11 logical Codex tasks across 12 executions, with an
estimated 125,306 tokens and 314.7 seconds of summed model latency. This is a
real quality-oriented run, not a mocked happy path. It also identifies a clear
optimization target before production-scale use.
