# Studio visual audit

Audit date: 2026-07-19

The pass covers the real Studio routes with the Camille synthetic finance fixture at desktop and mobile widths. Visual fixes are implemented in the shared design system. The items below are product or behavior follow-ups discovered during the pass; they were deliberately not mixed into the visual change.

| Surface | Visual status | Additional work discovered |
| --- | --- | --- |
| `/` | Unified hero, actions and active workspace card; removed artificial empty height. | Decide whether demo profiles belong on the public home after the hackathon demo. |
| `/setup` | Import and blank-profile choices use the same spacing, controls and card system. | Blank-profile creation is still a longer workflow than import; validate it separately with a non-technical user. |
| `/setup/imports/{id}` | Valid, failed and advanced mapping states are visually consistent; presets remain the primary choice. | The advanced block remains intentionally technical and should stay collapsed by default. |
| `/candidate-drafts/{id}` | Four-step editor, extracted evidence and sticky actions share one visual hierarchy. | Search free text can contain a dense machine-oriented brief; a later product pass should distinguish the user summary from advanced constraints. |
| `/profiles/{id}` | Campaign action, profile actions and current PDF are aligned and no longer inherit oversized panels. | The direct single-offer form remains an advanced path and should not compete with the primary campaign action. |
| `/profiles/{id}/applications` | Metrics, score explanations, application rows, tags and status pills are more readable. | Add filtering only when the tracker contains enough applications to justify it; legacy runs can still show no comparable baseline ATS score. |
| `/discoveries/{id}` | Progress cards, recovery state and diagnostics use the shared status language. | Completed discoveries redirect to their campaign, so the historical discovery screen is not independently inspectable after completion. |
| `/campaigns/{id}` | Native browser buttons were removed; completion, reserve and Chrome actions now form one hierarchy. | When reserve count is zero, reserve/retry actions should be hidden or disabled instead of remaining actionable. |
| `/runs/{id}` | Score cards, real PDF comparison, change ledger, agent trace and review are responsive and visually grouped. | The page is intentionally exhaustive and remains long; future simplification should collapse secondary strategy and agent detail without hiding evidence. |
| `/handoffs/{id}` | Artifact, policy and Chrome boundary cards now keep natural heights and readable filenames. | `Excel row` exposes the legacy tracker implementation; rename it to a generic tracker reference before public packaging. |
| `/sandbox/apply/{id}` | Live sandbox form uses the same typography, forms and status colors as Studio. | The sandbox proves artifact selection only; the UI must continue to label it as proof, not real employer submission. |
| `/sandbox/confirmation/{id}` | Template and responsive rules reviewed; no ready handoff was mutated only to manufacture a screenshot. | Recheck visually during the next real sandbox proof when a confirmation receipt already exists. |

## Shared visual rules now enforced

- One typography stack and one token set for color, borders, shadows and radii.
- Natural card height by default; fixed height only for document preview surfaces.
- Consistent primary, secondary and disabled buttons.
- Desktop and mobile layouts with no horizontal overflow on audited routes.
- Dense technical content is placed in details blocks; the primary user action remains visible.
- Cache-versioned Studio CSS so a running local server immediately loads the current visual system.
