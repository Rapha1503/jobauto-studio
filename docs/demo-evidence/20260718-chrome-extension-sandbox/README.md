# Chrome Extension sandbox evidence - 2026-07-18

This acceptance proof used the synthetic `Maya Laurent` profile and the user's
regular Chrome session controlled through the Codex Chrome Extension. It did
not use the in-app browser, Chrome for Testing, isolated Playwright or Computer
Use, and it did not submit an application to an employer.

## Proven path

1. Studio exposed the already reviewed handoff
   `handoff-8c6be17fce0ed466` in `claimed_for_chrome` state.
2. The Chrome Extension resumed the existing sandbox tab instead of creating a
   duplicate handoff.
3. Candidate identity, location and the approved cover-letter message were
   present in the form.
4. The browser file-chooser flow selected the exact reviewed `cv.pdf` and
   `letter.pdf` artifacts.
5. The sandbox received both files, rechecked their SHA-256 hashes and accepted
   the packet.
6. Studio persisted a `sandbox_verified` receipt with no blockers or warnings.
7. The real application tracker remained unchanged because a sandbox receipt
   is deliberately not an employer submission.

The browser confirmation is preserved in
[`confirmation.png`](confirmation.png), and the sanitized receipt plus exact
artifact hashes are recorded in [`receipt.json`](receipt.json).

## Observed prerequisite

Local PDF upload requires **Allow access to file URLs** in the ChatGPT Chrome
Extension details page. Navigation and form filling work without it, but the
file chooser cannot attach local artifacts. Studio, the bundled JobAuto skill
and the main README now surface this prerequisite.

## Boundary

This proves the real Chrome Extension control path, local file transfer, exact
artifact verification and receipt persistence. It does not claim that a live
career-site form was submitted. Live submissions remain subject to the
candidate's configured login, CAPTCHA, 2FA, consent and confirmation policy.
