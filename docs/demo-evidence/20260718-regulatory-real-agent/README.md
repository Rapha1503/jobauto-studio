# Regulatory cross-domain acceptance - 2026-07-18

This proof exercises the same candidate workflow used by JobAuto Studio with a
synthetic regulatory-affairs candidate and a synthetic medical-device offer.
All agent calls are real Codex CLI calls using `gpt-5.6-sol`; no personal data
or employer submission is involved.

## Defect under test

An earlier supervisor approved a truthful but generic cover letter. The letter
named the role and mapped relevant experience, but did not explain why the
sourced scope genuinely interested the candidate. A high aggregate score hid
that missing argument.

The generic review contract now evaluates five independent criteria:

- target specificity;
- evidence connected to central missions;
- explicit candidate contribution;
- credible motivation grounded in sourced work, scope or context;
- natural and professional tone.

Every passed criterion requires an exact excerpt from the rendered PDF. The
contract does not impose a paragraph template, word count or page-fill target.

## Discriminating controls

The patched supervisor was run directly against two frozen PDF packages:

- **negative control**: the old generic letter was rejected at 86/100;
  `target_specificity` and `motivation_credibility` required repair, with a
  letter-only repair action;
- **positive control**: the improved contextual letter was approved at 94/100
  with all five criteria supported by exact excerpts.

This establishes that the gate rejects the known defect without rejecting a
valid concise letter. `review-controls.json` contains both complete judgments;
the two event files preserve their model, latency, token estimates and hashes.

## Continuous final run

- six real Codex calls: offer analysis, brief review, CV writing, LaTeX
  projection, letter writing and final review;
- 60,413 estimated tokens and 160,961 ms of summed model latency;
- no brief or document repair required;
- one-page CV at 12 pt with 88.26% vertical coverage;
- one-page cover letter with a complete five-part argument;
- final score 96, ATS 98, editorial 95 and adaptation 97;
- unsupported continuous-improvement ownership and explicit priority-management
  claims remain warnings rather than invented evidence.

Both final PDFs were rendered at 144 DPI and inspected for typography,
clipping, overlap, spacing and page balance.

## Files

- `application-brief.json`: requirements, evidence mappings and strategy;
- `candidate-package.json`: exact CV patch, LaTeX projection and letter draft;
- `review.json`: final requirement coverage and five-part letter judgment;
- `review-controls.json`: frozen negative and positive supervisor controls;
- `agent-events.jsonl`: complete final-run agent telemetry;
- `negative-review-events.jsonl` and `positive-review-events.jsonl`: control telemetry;
- `regulatory-cv.pdf` and `regulatory-letter.pdf`: exact approved documents;
- PNG files: rendered visual inspection surfaces;
- `run-summary.json`: compact metrics and proof boundary.

## Boundary

This is a real-agent document-quality acceptance proof. It does not include web
discovery, Chrome control or an employer submission. Those capabilities have
separate evidence bundles and must not be inferred from this run.
