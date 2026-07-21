# Devpost submission record

Official sources checked on July 19, 2026:

- [OpenAI Build Week rules](https://openai.devpost.com/rules)
- [OpenAI Build Week](https://openai.com/build-week/)

JobAuto Studio was submitted in **Apps for Your Life** as a local application
for an individual job seeker's recurring workflow.

## Required assets

- [x] [public code repository](https://github.com/Rapha1503/jobauto-studio) with the MIT license;
- [x] [Devpost project](https://devpost.com/software/jobauto-studio) in Apps for Your Life;
- [x] [public YouTube demo](https://youtu.be/-1IRuEl3qJU), 2:51 with English audio;
- [x] README explaining Codex and GPT-5.6 usage;
- [x] representative Codex Session ID: `019f6795-4b44-7672-80cc-d327a03f344f`;
- [x] screenshots and checked workflow evidence.

The submitted English description is retained in
[`DEVPOST_DESCRIPTION.md`](DEVPOST_DESCRIPTION.md).

The public repository and MIT license provide the judging path.

## Existing project disclosure

JobAuto existed before the submission period. The submission must therefore
describe only the meaningful Build Week extension as the judged work and link
to [`BUILD_WEEK_SCOPE.md`](BUILD_WEEK_SCOPE.md), the representative Codex
`/feedback` Session ID and the captured GPT-5.6 agent traces. The public history
is intentionally a sanitized export and must not be presented as the sole proof
of the internal chronology. Do not present the earlier personal automation as
new work.

## Judging alignment

The four equally weighted criteria are:

1. technological implementation;
2. coherent product design and experience;
3. credible impact for a real audience;
4. quality and originality of the idea.

The video and description should demonstrate one piece of evidence for each
criterion rather than enumerate every internal module.

## `/feedback` Session ID

The submitted representative Codex session is
`019f6795-4b44-7672-80cc-d327a03f344f`.

## Three-minute demo

Use the timed [demo script](DEMO_SCRIPT.md) and the checked Maya Laurent
non-IT campaign. Do not combine unrelated runs in one claimed trace.

1. Import a synthetic `.tex` CV and show the original PDF.
2. Confirm the extracted profile and fidelity preset.
3. Start discovery and show structured offers without duplicates.
4. Open a campaign item and show ATS strategy, tailored CV, letter, review, and telemetry.
5. Show both PDFs on one page with their hashes.
6. Invoke the JobAuto Codex plugin, complete the sandbox through the Chrome
   extension, and show the accepted Studio receipt.
7. Close on the configurable candidate boundary and local privacy model.

Use only synthetic names in the recording. Hide real employer names and logos,
do not show unrelated browser tabs, and use no copyrighted music. All submitted
text, testing instructions and narration must be in English.

## Final gate

```powershell
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv build
uv run jobauto audit-release .
uv run jobauto audit-release dist\jobauto-0.1.0-py3-none-any.whl
uv run jobauto audit-release dist\jobauto-0.1.0.tar.gz
```

Also install the wheel in a fresh virtual environment and run `jobauto studio --no-open-browser` before recording the final demo.

Last verified on July 21, 2026: the wheel installed into a new temporary virtual
environment and both `/setup` and `/demo` returned HTTP 200 without importing
the source checkout.

The judge test path is: clone the repository, run the Quick start, click
`Explore the checked demo`, compare the packaged source and tailored PDFs, open
the five independent reviews and inspect the five saved sandbox receipts. This
replay is self-contained and makes no model call. The optional live Chrome test
requires installing the source repository's JobAuto plugin and opening one
reviewed packet in the local sandbox. Both paths must remain available free of
charge through the judging period.

Use [`demo-evidence/20260718-nonit-chrome-batch`](demo-evidence/20260718-nonit-chrome-batch/README.md)
as the canonical campaign proof set. The recording is a verified replay of that
completed campaign; show its telemetry and do not imply real-time generation.
Do not mix screenshots from different candidates or campaigns.
