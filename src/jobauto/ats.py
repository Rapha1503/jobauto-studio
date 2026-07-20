from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from jobauto.cv_source import CvSourceDocument
from jobauto.models import (
    ApplicationBrief,
    AtsScoreBreakdown,
    BaselineCvAssessment,
    CandidateApplicationReview,
    CandidateRepairAction,
    OfferRequirement,
    RenderedRequirementCoverage,
)

ATS_READY_SCORE = 85

_PRIORITY_WEIGHTS = {"must": 5.0, "important": 3.0, "nice": 1.0}
_COVERAGE_CREDITS = {
    "exact_term": {"exact": 1.0, "semantic": 0.55, "indirect": 0.20, "missing": 0.0},
    "semantic_concept": {
        "exact": 1.0,
        "semantic": 0.90,
        "indirect": 0.45,
        "missing": 0.0,
    },
    "structured_field": {
        "exact": 1.0,
        "semantic": 0.80,
        "indirect": 0.35,
        "missing": 0.0,
    },
}


def cv_source_text(document: CvSourceDocument) -> str:
    """Return the visible baseline content used by the ATS assessment."""
    parts = [document.name, document.headline, document.contact_line, document.summary]
    for entries in (document.experience, document.projects, document.education):
        for entry in entries:
            parts.extend([entry.title, entry.dates or "", entry.stack or "", *entry.bullets])
    for category, skills in document.skills.items():
        parts.extend([category, *skills])
    for section in document.additional_sections:
        parts.extend([section.label, section.content])
    parts.extend([document.languages, document.interests])
    return "\n".join(part for part in parts if part.strip())


def normalize_requirement_coverage(
    requirements: list[OfferRequirement],
    coverage: list[RenderedRequirementCoverage],
    document_text: str,
    *,
    require_excerpts: bool,
) -> list[RenderedRequirementCoverage]:
    """Ground coverage in the real CV and make exact-term matching deterministic."""
    requirements_by_id = {requirement.requirement_id: requirement for requirement in requirements}
    coverage_by_id = {item.requirement_id: item for item in coverage}
    if set(requirements_by_id) != set(coverage_by_id) or len(coverage) != len(coverage_by_id):
        raise ValueError("ATS coverage must contain every requirement exactly once")

    normalized_document = _normalize_match_text(document_text)
    normalized_items: list[RenderedRequirementCoverage] = []
    for requirement in requirements:
        item = coverage_by_id[requirement.requirement_id]
        exact_terms_present = [
            term
            for term in requirement.ats_terms
            if _contains_exact_term(normalized_document, term)
        ]
        grounded_excerpts = [
            excerpt
            for excerpt in item.supporting_excerpts
            if _normalize_match_text(excerpt) in normalized_document
        ]
        if len(grounded_excerpts) != len(item.supporting_excerpts):
            raise ValueError(
                f"ATS coverage excerpt is not present in the CV: {requirement.requirement_id}"
            )

        resolved_coverage = item.coverage
        resolved_excerpts = list(item.supporting_excerpts)
        if requirement.matching_mode == "exact_term":
            if exact_terms_present:
                resolved_coverage = "exact"
                if not resolved_excerpts:
                    resolved_excerpts = exact_terms_present[:3]
            elif item.coverage == "exact":
                raise ValueError(
                    "exact ATS coverage claimed without a requested term in the CV: "
                    f"{requirement.requirement_id}"
                )
        if resolved_coverage != "missing" and require_excerpts and not resolved_excerpts:
            raise ValueError(
                f"non-missing ATS coverage requires a CV excerpt: {requirement.requirement_id}"
            )
        if resolved_coverage == "missing" and resolved_excerpts:
            raise ValueError(
                f"missing ATS coverage cannot cite CV excerpts: {requirement.requirement_id}"
            )
        normalized_items.append(
            item.model_copy(
                update={
                    "coverage": resolved_coverage,
                    "supporting_excerpts": resolved_excerpts,
                }
            )
        )
    return normalized_items


def calculate_ats_readiness(
    requirements: list[OfferRequirement],
    coverage: list[RenderedRequirementCoverage],
    *,
    parseable: bool,
    role_positioning_matches: bool,
    language_matches: bool,
    improvable_requirement_ids: Iterable[str] = (),
) -> AtsScoreBreakdown:
    """Calculate JobAuto's versioned, explainable ATS-readiness estimate."""
    coverage_by_id = {item.requirement_id: item.coverage for item in coverage}
    if set(coverage_by_id) != {item.requirement_id for item in requirements}:
        raise ValueError("ATS scoring requires complete requirement coverage")

    weighted_total = 0.0
    weighted_earned = 0.0
    priority_scores: dict[str, int] = {}
    exact_total = 0.0
    exact_earned = 0.0
    critical_ids: list[str] = []
    weak_central_ids: list[str] = []
    improvable_ids = set(improvable_requirement_ids)

    for priority in ("must", "important", "nice"):
        group_total = 0.0
        group_earned = 0.0
        for requirement in requirements:
            if requirement.priority != priority:
                continue
            state = coverage_by_id[requirement.requirement_id]
            credit = _COVERAGE_CREDITS[requirement.matching_mode][state]
            weight = _PRIORITY_WEIGHTS[priority]
            group_total += weight
            group_earned += weight * credit
            weighted_total += weight
            weighted_earned += weight * credit
            if requirement.matching_mode == "exact_term":
                exact_total += weight
                exact_earned += weight * credit
            if priority == "must" and state == "missing":
                critical_ids.append(requirement.requirement_id)
            if priority in {"must", "important"} and (
                state in {"indirect", "missing"}
                or (requirement.matching_mode == "exact_term" and state != "exact")
            ):
                weak_central_ids.append(requirement.requirement_id)
        priority_scores[priority] = _percentage(group_earned, group_total)

    weighted_score = _percentage(weighted_earned, weighted_total)
    exact_score = _percentage(exact_earned, exact_total) if exact_total else None
    score = weighted_score if parseable else 0
    ready = (
        score >= ATS_READY_SCORE
        and not critical_ids
        and not weak_central_ids
        and parseable
        and role_positioning_matches
        and language_matches
    )
    weak_improvable = sorted(
        requirement_id
        for requirement_id in set(weak_central_ids) | set(critical_ids)
        if requirement_id in improvable_ids
    )
    any_improvable_gap = any(
        coverage_by_id[requirement_id] != "exact" and requirement_id in improvable_ids
        for requirement_id in coverage_by_id
    )
    adaptation_recommended = not ready and (
        not parseable
        or not role_positioning_matches
        or not language_matches
        or bool(weak_improvable)
        or (score < ATS_READY_SCORE and any_improvable_gap)
    )

    reasons: list[str] = []
    if not parseable:
        reasons.append("The CV is not reliably parseable.")
    if not role_positioning_matches:
        reasons.append("The headline or summary does not position the requested role clearly.")
    if not language_matches:
        reasons.append("The CV language does not match the configured application language.")
    if critical_ids:
        reasons.append("Missing must requirements: " + ", ".join(sorted(critical_ids)) + ".")
    if weak_central_ids:
        reasons.append(
            "Weak central requirement coverage: " + ", ".join(sorted(weak_central_ids)) + "."
        )
    if score < ATS_READY_SCORE:
        reasons.append(f"Readiness {score}/100 is below the JobAuto target {ATS_READY_SCORE}.")
    if not reasons:
        reasons.append("The CV is parseable and covers every central requirement strongly.")

    return AtsScoreBreakdown(
        score=score,
        weighted_requirement_score=weighted_score,
        exact_term_score=exact_score,
        priority_scores=priority_scores,
        critical_requirement_ids=sorted(critical_ids),
        weak_central_requirement_ids=sorted(weak_central_ids),
        parseable=parseable,
        role_positioning_matches=role_positioning_matches,
        language_matches=language_matches,
        ready_without_cv_changes=ready,
        adaptation_recommended=adaptation_recommended,
        decision_reasons=reasons,
    )


def normalize_baseline_assessment(
    brief: ApplicationBrief,
    document_text: str,
    *,
    require_excerpts: bool = True,
) -> ApplicationBrief:
    assessment = brief.baseline_cv_assessment
    if assessment is None:
        raise ValueError("baseline CV assessment is required")
    coverage = normalize_requirement_coverage(
        brief.requirements,
        assessment.requirement_coverage,
        document_text,
        require_excerpts=require_excerpts,
    )
    breakdown = calculate_ats_readiness(
        brief.requirements,
        coverage,
        parseable=bool(document_text.strip()),
        role_positioning_matches=assessment.role_positioning_matches,
        language_matches=assessment.language_matches,
        improvable_requirement_ids=assessment.improvable_requirement_ids,
    )
    decision = "adapt" if breakdown.adaptation_recommended else "keep_baseline"
    if decision == "adapt":
        material_gaps = list(assessment.material_gaps) or breakdown.decision_reasons
        improvable_ids = sorted(set(assessment.improvable_requirement_ids))
        rationale = (
            f"JobAuto ATS readiness is {breakdown.score}/100. "
            "The CV has material, improvable visibility gaps, so targeted adaptation is useful."
        )
    else:
        material_gaps = []
        improvable_ids = []
        if breakdown.ready_without_cv_changes:
            rationale = (
                f"JobAuto ATS readiness is {breakdown.score}/100 with no material central gap; "
                "rewriting the CV would add no justified value."
            )
        else:
            rationale = (
                f"JobAuto ATS readiness is {breakdown.score}/100, but the remaining gaps cannot "
                "be improved from permitted candidate evidence; rewriting would be cosmetic."
            )
    normalized = BaselineCvAssessment(
        decision=decision,
        ats_score=breakdown.score,
        ats_breakdown=breakdown,
        confidence="high",
        role_positioning_matches=assessment.role_positioning_matches,
        language_matches=assessment.language_matches,
        material_gaps=material_gaps,
        improvable_requirement_ids=improvable_ids,
        requirement_coverage=coverage,
        rationale=rationale,
    )
    return brief.model_copy(update={"baseline_cv_assessment": normalized})


def normalize_final_review(
    review: CandidateApplicationReview,
    brief: ApplicationBrief,
    document_text: str,
    *,
    require_excerpts: bool = True,
) -> CandidateApplicationReview:
    baseline = brief.baseline_cv_assessment
    improvable_ids = baseline.improvable_requirement_ids if baseline is not None else []
    coverage = normalize_requirement_coverage(
        brief.requirements,
        review.requirement_coverage,
        document_text,
        require_excerpts=require_excerpts,
    )
    breakdown = calculate_ats_readiness(
        brief.requirements,
        coverage,
        parseable=bool(document_text.strip()),
        role_positioning_matches=True,
        language_matches=True,
        improvable_requirement_ids=improvable_ids,
    )
    data = review.model_dump(mode="python")
    data.update(
        {
            "ats_score": breakdown.score,
            "ats_breakdown": breakdown,
            "requirement_coverage": coverage,
        }
    )
    blockers = list(review.blocking_issues)
    repairs = list(review.repair_actions)
    if review.approved and breakdown.adaptation_recommended:
        blockers.append(
            "The final CV still has a material ATS visibility gap that the candidate evidence can improve."
        )
        repairs.append(
            CandidateRepairAction(
                surface="cv",
                instruction=(
                    "Repair only the remaining improvable central requirement coverage while "
                    "preserving every accepted CV and letter element."
                ),
            )
        )
    regressed_without_closing_central_gaps = bool(
        review.approved
        and baseline is not None
        and baseline.ats_breakdown is not None
        and baseline.decision == "adapt"
        and breakdown.score < baseline.ats_score
        and len(breakdown.weak_central_requirement_ids)
        >= len(baseline.ats_breakdown.weak_central_requirement_ids)
    )
    if regressed_without_closing_central_gaps:
        blockers.append(
            "The tailored CV lowered ATS readiness without resolving more central requirements."
        )
        repairs.append(
            CandidateRepairAction(
                surface="cv",
                instruction=(
                    "Restore the stronger baseline coverage and change only content that closes a "
                    "documented central requirement gap."
                ),
            )
        )
    if blockers:
        data.update({"approved": False, "blocking_issues": blockers, "repair_actions": repairs})
    return CandidateApplicationReview.model_validate(data)


def _percentage(earned: float, total: float) -> int:
    if total <= 0:
        return 100
    return round(100 * earned / total)


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    ascii_value = re.sub(r"(?<=\w)-\s+(?=\w)", "", ascii_value)
    return " ".join(re.sub(r"[^a-z0-9+#./]+", " ", ascii_value).split())


def _contains_exact_term(normalized_document: str, term: str) -> bool:
    normalized_term = _normalize_match_text(term)
    if not normalized_term:
        return False
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])",
            normalized_document,
        )
        is not None
    )
