# JobAuto ATS readiness

JobAuto does not claim to reproduce a universal applicant tracking system score. No such
score exists across vendors or employers.

Official product documentation shows several distinct mechanisms:

- Greenhouse supports full-text resume search, Boolean operators, exact phrases and
  wildcards.
- Greenhouse Talent Rediscovery distinguishes required keywords (`AND`) from preferred
  keywords (`OR`) and documents exact keyword matching.
- Oracle intelligent matching exposes separate profile, education, experience and skills
  criteria.
- Oracle iRecruitment documents required and desirable skills, with a missing essential
  skill capable of producing a mismatch.

Sources:

- https://support.greenhouse.io/hc/en-us/articles/202360199-Search-candidates-using-Boolean-queries
- https://support.greenhouse.io/hc/en-us/articles/30184390692379-Talent-Rediscovery
- https://docs.oracle.com/en/cloud/saas/talent-management/faush/understand-suggested-candidates.html
- https://docs.oracle.com/cd/E26401_01/doc.122/e59063/T422283T580098.htm#Calculating-Skills-Match-Percentage

## JobAuto model

`jobauto_ats_readiness_v1` is a comparable internal estimate. It evaluates the same
sourced offer requirements before and after CV adaptation.

The offer-understanding agent supplies structured judgments, not the score:

- one independently useful requirement per `requirement_id`;
- priority: `must`, `important`, or `nice`;
- matching mode: `exact_term`, `semantic_concept`, or `structured_field`;
- exact ATS terms copied from the offer when literal matching matters;
- visible CV coverage: `exact`, `semantic`, `indirect`, or `missing`;
- exact supporting excerpts copied from the real CV.

The deterministic scorer then applies:

| Dimension | Values |
| --- | --- |
| Priority weight | must 5, important 3, nice 1 |
| Exact term | exact 1.00, semantic 0.55, indirect 0.20, missing 0 |
| Semantic concept | exact 1.00, semantic 0.90, indirect 0.45, missing 0 |
| Structured field | exact 1.00, semantic 0.80, indirect 0.35, missing 0 |

The weighted result is rounded to 0-100. A separate breakdown exposes priority scores,
literal-term coverage, critical gaps and weak central requirements. A PDF that cannot be
parsed receives a score of zero.

The weights are JobAuto's documented heuristic, not a secret vendor formula. They are
versioned so benchmark calibration can change them without silently changing historical
scores.

## Rewrite decision

CV adaptation is not triggered by the number alone.

The baseline is kept when it is parseable, correctly positioned and already covers every
central requirement strongly with a readiness of at least 85. Minor possible edits do not
justify rewriting.

The CV is adapted only when readiness is insufficient **and** a visible problem can be
improved from permitted candidate evidence, for example:

- a central named term is absent although a defensible skill can expose it;
- a supported central mission is only indirectly visible;
- headline or language positioning is wrong;
- the document is not reliably parseable.

If the score is low only because the candidate genuinely lacks an unsupported requirement,
rewriting is cosmetic and is skipped. That fit problem belongs to offer selection, not CV
keyword stuffing.

After rendering, the supervisor supplies fresh grounded coverage. JobAuto recalculates the
same score. An adapted CV that lowers readiness without resolving more central gaps is
rejected for repair.
