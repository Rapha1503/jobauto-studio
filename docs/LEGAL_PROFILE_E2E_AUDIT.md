# Legal profile end-to-end audit

## Scope

This audit exercises JobAuto Studio with a synthetic, non-IT legal profile. The goal is to verify that the public workflow is configurable outside data and engineering roles, not to tune a legal-specific branch.

Tested path:

`LaTeX import -> profile review -> preferences -> discovery -> ATS strategy -> CV and letter -> PDF inspection -> application queue -> Chrome handoff contract -> sandbox receipt`

The employer and identity used by the final controlled run are synthetic. No real employer application was submitted.

## Profile lifecycle

| User action | Result | Verdict |
| --- | --- | --- |
| Create another profile | Opens a clean setup without overwriting the current workspace | Pass |
| Open another profile | Persists it as the current profile | Pass |
| Edit profile and preferences | Reopens the original draft instead of restarting import | Pass |
| Archive profile | Hides it while preserving runs, campaigns and receipts | Pass |
| Restore profile | Returns it to the profile chooser | Pass |
| Archive and start over | Archives the profile and opens a clean setup | Pass |
| Permanently delete profile | Not implemented | Product decision required |

Permanent deletion is intentionally different from reset. Runs, tracker rows and receipts reference a candidate identifier. A safe delete needs an explicit retention policy rather than recursive filesystem deletion from the UI.

## Configuration coverage

Every user-facing configuration family was exercised at least once.

### CV import and block rules

- Conservative, balanced and flexible presets.
- Locked, light, adaptable, strong and replaceable block fidelity.
- Required and optional blocks.
- Target line budgets.
- Manual block addition and removal.
- Summary, experience, projects, skills, education, languages, interests and an additional section.

Finding: the rules are expressive, but the line-number-oriented advanced editor is too technical for the default journey. It should remain an advanced recovery surface.

### Experiences and evidence

- Two experiences with organizations, roles, dates, facts and verified metrics.
- Protected and unprotected metrics.
- Multiple allowed angles: legal analysis, operations, compliance, stakeholder support and process design.
- Additional education entries and a custom association section.

Finding: experience dates were initially absent from exported evidence. This caused a false terminal experience-gap rejection. Dates are now exported as verified facts without being treated as protected metrics.

### Skills

- Required, default and removable skills.
- Verified, transferable and forbidden evidence levels.
- Verification warnings for transferable additions.
- Addition and removal of a skill.

Finding: the strategy can operate on legal methods and tools, but the final reviewer missed two semantically duplicate category labels. Category deduplication remains a generic review gap.

### Projects and Project Lab

- Reuse, reframe and derive modes.
- Independent title, stack and description fidelity.
- CV visibility on and off.
- Project add and remove.
- New-project permission, external-inspiration permission and visible-project limits.

Finding: the controls work outside IT, but the UI still exposes more project governance than a first-time user needs. The simple path should explain the chosen project policy in plain language and keep field-level controls collapsed.

### Search preferences

- Free-form intent.
- Required, preferred and avoided roles.
- Announcement keywords, occupation tools, sectors, locations and contracts.
- Maximum experience, offer age, salary and remote preference.
- Company and title exclusions.

The real discovery run inspected four current candidates. One offer was selected, two were rejected by the configured experience ceiling and one by location. This proves filtering and ranking, but not that every discovery source will always provide complete dates or seniority.

### Submission preferences

- Automatic, confirm-before-submit and dry-run modes.
- Campaign limit, standard answers, retries, allowed portal and consent policy.
- Login, CAPTCHA, 2FA and ambiguous-field behaviors.
- Required confirmation evidence.

Dry run produced no browser navigation. Automatic mode produced a claimable handoff. The packet was released back to the queue and claimed again.

## Controlled final run

Synthetic offer: Juriste conformite et contrats.

- Status: completed.
- Baseline CV ATS readiness: 86.
- Final CV ATS readiness: 86.
- Final supervisor score: 94.
- Adaptation score: 97.
- CV: one page, 11 pt, 90.14 percent vertical coverage.
- Letter: one page, concise, with substantial unused lower-page space.
- Documents: role- and company-specific filenames.
- Queue: completed direct run attached without regeneration.
- Tracker: offer, CV link and letter link synchronized.
- Handoff: exact artifact paths, SHA-256 hashes and candidate form profile persisted.
- Sandbox: exact CV and letter hashes accepted; `sandbox_verified` receipt persisted.

The direct-offer path previously stopped after document generation. A completed direct run can now enter a one-item campaign without a second generation call or a duplicate tracker row.

## Observability and controls

| Stage | Where to observe | Control |
| --- | --- | --- |
| Profile setup | Draft steps and validation messages | Return to the saved draft |
| Job discovery | Discovery page with live candidate count and event trace | Stop search, resume search |
| Document campaign | Campaign page with offer rows, run phase, calls, repairs, time and tokens | Stop after current application, resume |
| One-off document run | Run page with phase history, agent trace, ATS review and PDFs | No cooperative cancel yet |
| Application portfolio | Applications dashboard with offer, fit, ATS, changes, documents and status | Open the run or campaign |
| Chrome queue | Campaign submission summary and handoff page | Return a claimed packet to the queue |
| Submission proof | Handoff receipt and applications dashboard | Receipt is immutable evidence |

Search and campaign cancellation are cooperative. A running agent call finishes before the workflow stops. A one-off direct document run has no cancel endpoint yet; this is the main remaining interaction gap.

## Defects found and fixed during the audit

1. Completed direct runs could not reach the application queue.
2. Experience dates were not exported as evidence.
3. Missing optional education locations were serialized as the text `None`.
4. Old profile versions cluttered the profile chooser.
5. Tailored PDF previews could remain on the fallback after generation completed.
6. Campaign refresh errors were hidden behind a generic reconnecting message.
7. Submission-mode buttons did not restore their labels after dry run.
8. Sandbox file reading could remain indefinitely on a verification message.

## Remaining limits

- The profile setup is still too dense for a first-time user.
- Permanent delete is absent; archive and reset are the safe lifecycle actions.
- Direct one-off generation cannot be cancelled while an agent call is active.
- The legal run used 14 logical agent calls and about 240k estimated tokens. It is correct but too expensive for a normal happy path.
- The reviewer needs a generic semantic deduplication pass for competency category labels.
- The controlled in-app browser could select local PDFs but could not expose their bytes to page JavaScript. The sandbox endpoint and hashes passed through the API, so server verification is proven; browser upload through the real Chrome Extension is not proven by this run.
- Real employer submission was deliberately not attempted with the synthetic profile.

## Verification

Focused Studio, export, discovery, campaign, handoff and run-store validation: 92 tests passed. The complete repository suite then passed 316 tests. Ruff and `git diff --check` also passed.
