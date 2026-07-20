# JobAuto Studio

**Track:** Apps for Your Life

**Tagline:** A candidate-controlled workflow that takes a trusted CV from job discovery to reviewed documents and a traceable Chrome application handoff.

## Inspiration

Applying to jobs is repetitive, but blindly rewriting a CV is risky. Candidates
need speed without losing control of their facts, layout or voice. JobAuto turns
that work into one observable local workflow while keeping the candidate in
charge of what may change.

## What it does

JobAuto accepts three candidate-owned starting points: a LaTeX CV with exact
source preservation, a PDF whose selectable text is extracted with page-level
provenance, or a profile created block by block without a source file. The user
reviews the resulting profile and chooses simple fidelity rules for each
section. Codex then discovers and ranks job offers, removes duplicates, analyzes
ATS requirements and maps them to candidate evidence.

For each selected offer, JobAuto plans the application angle, tailors the CV and
cover letter, compiles the real PDFs, reviews the rendered documents and repairs
problems. Deterministic checks enforce source integrity, one-page limits, hashes
and artifact validity. The final package enters an idempotent queue consumed by
the JobAuto Codex plugin through the user's Chrome session.

## How we built it

The product uses Python, FastAPI, Jinja, filesystem-backed run stores, an Excel
tracker, LaTeX, PDF inspection and an installable Codex plugin. Codex CLI is the agent runtime.
GPT-5.6 performs offer analysis, application strategy, writing, independent
review and repair. Studio exposes the model, phases, latency, token estimates,
scores, decisions and final artifact hashes instead of hiding the agent loop.

Codex was also the development partner. It inspected the earlier personal
workflow, implemented the generic candidate boundary and Studio interface,
tested real PDFs and browser handoffs, reviewed the UX and hardened the public
release. The main decisions were to preserve a candidate-owned `.tex` when
available, generate a controlled layout for PDF and manual profiles, separate
deterministic document guarantees from agentic content decisions, and keep live
browser control in a Codex plugin rather than inside the web server.

## Build Week extension

JobAuto existed before Build Week as a personal command-line automation. During
the submission period it was meaningfully extended into the generic product in
this repository: configurable profiles and evidence, visual onboarding,
candidate-isolated campaigns, public packaging, release audits, synthetic test
profiles, the Chrome queue/plugin contract and end-to-end public evidence. The
dated checkpoints are documented in `docs/BUILD_WEEK_SCOPE.md`.

## Challenges

The hardest problem was not generating text. It was preventing a plausible
draft from becoming an unsupported claim or the wrong file from reaching a
form. JobAuto snapshots candidate evidence, records every agent phase, reviews
the final rendered PDF and rechecks hashes when Chrome claims a packet. A stale
pre-repair cover letter is rejected even when its filename looks correct.

## What we are proud of

The checked non-technical acceptance campaign starts from a synthetic cultural
production profile, ranks nine offers and completes five different application
packages. It produces ten one-page PDFs, five independent final reviews with
scores from 90 to 92, five model traces and five hash-verified Chrome sandbox
receipts. This shows that the product is not tied to a Data or software CV.

## Testing

The repository includes deterministic tests for candidate isolation, LaTeX
preservation, PDF rendering, ATS strategy, review and repair, discovery,
campaigns, plugin queue behavior, receipts and release privacy. A separate
captured real-agent evidence pack contains the final PDFs, reviews, GPT-5.6
phase traces and SHA-256 manifest. Judges can clone the repository, open the
included demo profile and exercise the local submission sandbox for free.

## Current boundary

The public demo proves authenticated Chrome control, field completion, exact
file upload and receipt persistence against JobAuto's sandbox. It does not claim
that the sandbox receipt is an employer submission. Real career sites remain
subject to the candidate's login, CAPTCHA, 2FA, consent and submission policy.

## What's next

Next steps are easier installer packaging and a larger library of portal-specific
Chrome recovery patterns while preserving the same candidate-controlled evidence
boundary.

## Submission links

- Repository: `https://github.com/Rapha1503/jobauto-studio`
- Demo video: `https://youtu.be/wq0uEt9Gfak`
- Codex `/feedback` Session ID: `<session ID>`
