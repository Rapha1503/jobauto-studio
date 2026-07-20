# JobAuto Studio

JobAuto is a local application for configuring and running an AI-assisted job application workflow with Codex and GPT-5.6.

Import a LaTeX or PDF CV, or build one block by block, then review the candidate profile, define what may change, discover relevant roles, generate tailored documents, inspect the real PDFs, and prepare a traceable Chrome submission handoff.

## What works

- candidate-owned `.tex` import with exact source preservation;
- PDF content import with page-level provenance and mandatory profile review;
- file-free manual onboarding for work, research, projects, education, skills,
  languages and candidate-named sections;
- automatic profile, experience, project, skill, education, and preference extraction;
- configurable fidelity for every CV section;
- candidate evidence, project bank, transferable-skill policy, and Project Lab;
- batch discovery handoff, deduplication, ranking, and campaign tracking;
- ATS/application brief, tailored CV and letter, reviewer, repair loop, and telemetry;
- candidate-facing explanation of the planned CV angle, letter angle, project strategy,
  competencies, ATS signals, and evidence-backed adaptation decisions;
- one-page LaTeX/PDF rendering with semantic and identity checks;
- source-preserving adaptation for arbitrary CV sections such as certifications,
  publications, portfolios, awards, memberships or volunteering; neither a
  Skills section nor a Projects section is mandatory;
- automatic readability fitting: JobAuto maximizes font size and line height,
  then adds bounded inter-section spacing only when a one-page CV remains
  underfilled at the configured ceiling;
- idempotent campaign-level Chrome handoff queue, deterministic submission sandbox, and receipts;
- installable Codex plugin that consumes reviewed packets through the user's Chrome session.

The public package contains only the generic Studio path and synthetic examples. Candidate facts are loaded from the profile created by the user. No personal profile or private application history is bundled.

## Built with Codex and GPT-5.6

JobAuto started as a personal command-line workflow. During OpenAI Build Week,
Codex and GPT-5.6 were used to turn it into the generic Studio product in this
repository: candidate-owned profiles, visual onboarding, configurable
adaptation policies, isolated campaigns, public packaging, the JobAuto plugin
and synthetic end-to-end evidence. The dated scope is recorded in
[`docs/BUILD_WEEK_SCOPE.md`](docs/BUILD_WEEK_SCOPE.md).

Codex inspected the existing pipeline, implemented and tested the product
changes, reviewed the real UI and PDFs, and drove release hardening. GPT-5.6 is
also part of the running product: it performs offer analysis, application
strategy, document writing, independent review and repair. Its model name,
phase outcomes, latency and token estimates are exposed in Studio and in the
public evidence pack.

The main product decisions were human-directed and Codex-implemented:

- preserve an imported `.tex` as the layout source of truth, or use JobAuto's
  generated one-page template for a manually created profile;
- keep page limits, hashes and source integrity deterministic while allowing
  agents to reason about roles, evidence, ATS signals and wording;
- keep live browser control outside FastAPI in an installable Codex plugin;
- use deterministic agent adapters in CI and publish captured real-agent traces
  separately, rather than hide model calls behind an unreliable test.

## Quick start

Requirements:

- Python 3.11 or newer;
- [uv](https://docs.astral.sh/uv/);
- a working LaTeX distribution with `pdflatex`;
- Codex CLI authenticated on the machine.

```powershell
git clone https://github.com/Rapha1503/jobauto-studio.git
cd jobauto-studio
uv sync --extra dev
uv run jobauto studio
```

Open `http://127.0.0.1:8765` if the browser does not open automatically.

Select **Explore the checked demo** to inspect the packaged, synthetic Maya
Laurent campaign without spending tokens or contacting an employer. It includes
the original English CV, five tailored CVs and letters, final reviews, model
traces, hashes and five sandbox receipts. **Create my profile** starts the live
workflow with the current user's own CV and preferences.

The complete Studio path is tested on Windows 11 with Codex CLI/Desktop and the
ChatGPT Chrome Extension. The FastAPI Studio and document pipeline require
Python 3.11+, `uv` and LaTeX; the browser-submission proof uses the Windows
Chrome integration.

The source repository is the complete distribution because it also contains the
Codex plugin. The Python wheel contains Studio and the document pipeline, but
not the plugin marketplace. For browser submission from a source clone, add the
repository as a Codex marketplace and install the **JobAuto** plugin:

```powershell
codex plugin marketplace add .
codex plugin add jobauto@jobauto-studio
```

The plugin reads only the local Studio queue and directs Codex to use the
authenticated Chrome extension. In a Codex task, provide the Studio campaign
URL and ask JobAuto to process its ready packets.

Before the first PDF upload, open `chrome://extensions`, select the ChatGPT
Chrome Extension, open **Details**, and enable **Allow access to file URLs**.
Without that permission, Chrome navigation and form filling still work but the
CV and cover-letter selectors remain empty.

Use a different model or state directory when needed:

```powershell
uv run jobauto studio --codex-model gpt-5.6-sol --state-root .jobauto-state
```

`JOBAUTO_CODEX_MODEL` provides the same model setting. The selected model is displayed in Studio and recorded in agent telemetry.

## Main flow

1. **Create profile**: upload a `.tex` for exact source preservation, import selectable text from a PDF, or build the CV from reusable blocks without a source file.
2. **Review profile**: confirm extracted facts and choose a simple fidelity preset.
3. **Search**: define target roles and optional filters; Codex produces a structured discovery handoff.
4. **Campaign**: check offer reachability, retain explicit verification provenance,
   deduplicate offers, and launch selected applications. Codex identifies a primary
   page; the local HTTP check proves availability, not employer ownership.
5. **Documents**: analyze ATS requirements, choose evidence/projects, write, render, review, and repair.
6. **Apply**: the JobAuto Codex plugin reads each exact packet, uses the user's Chrome session, and stores the resulting receipt.

The user controls facts and permissions. Deterministic code controls source integrity, page limits, hashes, and artifact validity. Codex controls offer understanding, content strategy, writing, review, and repair.

### PDF input boundary

A PDF is supported as an extraction source, but it is not treated as an editable
layout source: fonts, reading order, columns and drawing commands do not reconstruct
into the original LaTeX. JobAuto follows **PDF -> page text with provenance ->
candidate draft -> user review -> generated template**. A scanned PDF without useful
selectable text falls back to the manual editor. Exact visual preservation remains
available only for `.tex`; JobAuto never presents approximate PDF-to-LaTeX conversion
as source fidelity.

## Privacy boundary

Run the release audit on the source tree or built wheel:

```powershell
$env:JOBAUTO_RELEASE_DENY_TERMS="private name|private employer|private email"
uv run jobauto audit-release .
uv build
uv run jobauto audit-release dist\jobauto-0.1.0-py3-none-any.whl
```

The audit checks every distributable text member for configured deny terms, non-example emails, phone numbers, user-specific Windows paths, private keys, and API-secret patterns.

## Verification

```powershell
uv run pytest -q
uv run ruff check .
uv build
```

The automated suite covers candidate isolation, prompt inputs, source-preserving LaTeX, real PDF compilation, reviewer/repair behavior, discovery, campaigns, handoffs, and the release audit.
The development environment runs four module-scoped pytest workers so the real
LaTeX/PDF acceptance tests remain isolated while completing in a practical time.
CI uses deterministic agent adapters rather than spending live Codex calls on
every push. The canonical evidence below contains the captured `gpt-5.6-sol`
phase traces, reviews, hashes and final PDFs from a real-agent batch.

The canonical public demo is documented in
[`docs/demo-evidence/20260718-nonit-chrome-batch`](docs/demo-evidence/20260718-nonit-chrome-batch/README.md).
It covers a non-IT cultural-production profile, nine ranked offers, five selected
and independently supervised application packages, ten one-page PDFs, hashes,
agent telemetry and five verified sandbox handoffs. The
[`atomic proof`](docs/demo-evidence/20260718-atomic-e2e/README.md) remains the
smallest continuous single-application trace. A separate
[`Chrome Extension proof`](docs/demo-evidence/20260718-chrome-extension-sandbox/README.md)
records a real browser-controlled upload of the exact approved PDFs and the
resulting sanitized sandbox receipt.
The [`regulatory cross-domain acceptance`](docs/demo-evidence/20260718-regulatory-real-agent/README.md)
adds a real-agent negative/positive control for cover-letter argument quality:
the patched supervisor rejects the frozen generic letter and approves the
contextual alternative before a fresh continuous document run is accepted.

## Current boundary

Studio prepares one persistent handoff per completed campaign run, reports which packets are ready, blocked, waiting or submitted, and demonstrates the complete form flow in its local sandbox. Repeating queue preparation or restarting Studio does not duplicate a handoff. The bundled Codex plugin is the browser-session-aware controller: it consumes the local queue, uses the user's Codex Chrome Extension session and writes receipts back to Studio. Login, CAPTCHA, 2FA and ambiguous consent remain governed by the candidate's submission policy.

See the [demo runbook](docs/DEMO_RUNBOOK.md), [Build Week scope](docs/BUILD_WEEK_SCOPE.md)
and [Devpost checklist](docs/DEVPOST_SUBMISSION.md).

## License

MIT. See [LICENSE](LICENSE).
