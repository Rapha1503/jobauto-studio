# JobAuto Studio demo runbook

The canonical checked journey is preserved in
[`docs/demo-evidence/20260718-nonit-chrome-batch`](demo-evidence/20260718-nonit-chrome-batch/README.md).
It contains one synthetic non-IT candidate, nine ranked offers, five completed
application packages and five verified sandbox receipts. The recording replays
this completed campaign and shows its real telemetry; it does not imply that
five agent runs finish during the three-minute video.

## Demonstrable path

### Recorded three-minute path

1. Start Studio and click **Explore the checked demo**.
2. Establish the synthetic candidate, nine-offer batch and five selected
   applications from the summary at the top of the page.
3. Compare the immutable English source CV and one tailored CV side by side.
4. Open one tailored letter, final review and agent trace from the same
   application card.
5. Show that every card exposes final scores, model executions, token estimates,
   one-page PDFs and an exact sandbox receipt.
6. Explain that the replay is packaged evidence and makes no model call.
7. Open a live Studio setup page to show the three candidate inputs and the
   verified Codex, LaTeX, Chrome-control and JobAuto-plugin prerequisites.
8. Optionally invoke the plugin against one sandbox packet to demonstrate live
   Chrome control. Do not imply an employer submission.

### Full product path

1. Import a self-contained UTF-8 `.tex` CV.
2. Preserve and hash the exact source and preamble.
3. Compile and display the original PDF.
4. Extract profile facts and every source-defined section. Projects and skills
   are used when the imported CV contains them; domain-specific sections remain
   first-class candidate evidence instead of being collapsed into an IT schema.
5. Let the candidate review simple defaults and open advanced controls only
   when needed.
6. Launch Codex web discovery from candidate-owned search preferences.
7. Ask Codex to open detailed primary offer pages, then independently check HTTP
   reachability, record whether that check succeeded or remained unknown,
   deduplicate, filter and rank the returned offers.
8. Append selected offers to an isolated `.xlsx` tracker.
9. Build the ATS brief and evidence mapping.
10. Select, reframe, derive or create projects according to candidate policy.
11. Generate the CV and letter, compile the real PDFs, review and repair them.
12. Compare the immutable original-run CV and tailored CV, then inspect the
    planned CV and letter angles, project strategy, competencies and ATS signals
    beside the final supervisor assessment on the completed run page.
13. Persist scores, requirements coverage, hashes, page counts, layout metrics,
    agent phases, latency and token estimates.
14. Prepare the campaign-level Chrome queue. It creates or reuses one exact
    handoff per completed run with final rehashed PDFs and candidate policy.
15. Show aggregate ready, submitted, blocked and waiting counts, then open one
    handoff packet.
16. Exercise the upload and receipt contract in the deterministic local
    sandbox.
17. Invoke the bundled JobAuto Codex plugin with the campaign URL. It fetches
    the next exact packet, uses the user's Chrome extension and writes the
    resulting receipt back to Studio.

The local app does not impersonate a third-party portal. It prepares and tracks
the campaign queue but does not control Chrome directly. The bundled Codex
plugin is the external, browser-session-aware controller. It communicates with
Studio only through localhost, while Codex executes the live form through the
user's Chrome Extension.

## Run from source

```powershell
uv sync --extra dev
uv run pytest -q
uv run jobauto studio --state-root ".jobauto-demo" --profiles-root "config/profiles"
```

Open `http://127.0.0.1:8765`. Use only synthetic profiles and offers for the
public demo.

Install the repository marketplace and JobAuto plugin before the Chrome segment:

```powershell
codex plugin marketplace add .
codex plugin add jobauto@jobauto-studio
```

For the public recording, point Chrome at JobAuto's sandbox rather than
submitting to a real employer. Refresh the setup page after installing the
plugins: all four local prerequisites must report **Ready** before recording the
live Chrome segment.

The checked Chrome Extension upload and receipt proof is preserved in
[`docs/demo-evidence/20260718-chrome-extension-sandbox`](demo-evidence/20260718-chrome-extension-sandbox/README.md).

## Supporting atomic proof

The latest verified trace used:

- discovery `alex-morgan-6158bd194e2e`, five official candidates;
- campaign `alex-morgan-58156fc74af2`;
- run `alex-morgan-03858495e568`;
- first reviewer rejection at 58/100 for an unsupported completed LLM project;
- repaired final score 91, ATS 93, editorial 94, adaptation 91;
- one-page CV, 2,895 extracted characters, vertical coverage 0.8661;
- one-page letter, 1,252 extracted characters, vertical coverage 0.3399;
- handoff `handoff-999bf0511b2e4077` with both approved hashes rechecked;
- deterministic `sandbox_verified` receipt without changing the real
  application status in the tracker.

The run required 11 logical Codex tasks, 12 executions, approximately 125,306
tokens and 314.7 seconds of summed model latency. This is intentionally exposed
rather than presented as production-efficient.

## Release validation

```powershell
uv run pytest -q
uv run ruff check .
uv build
uv run jobauto audit-release .
uv run jobauto audit-release "dist\jobauto-0.1.0-py3-none-any.whl"
```

Install the wheel into a clean virtual environment. Confirm that both packaged
synthetic profiles load and that a preview PDF renders without access to the
source repository.

## Visual checks

The automated gate checks actual PDF bytes, page count, extracted text,
identity, contacts, semantic content, hashes and layout metrics. Before the
recorded demo, also render both final PDFs to images and inspect typography,
spacing, clipping and page balance.
