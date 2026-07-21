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
RESIDUAL_ATS_GAP_BLOCKER = (
    "The final CV still has a material ATS visibility gap that the candidate evidence can improve."
)
RESIDUAL_ATS_GAP_WARNING = (
    "A residual ATS visibility gap remains after the configured repair budget; "
    "the package keeps the strongest truthful evidence available."
)
_RESIDUAL_ATS_REPAIR_PREFIX = "Repair only these remaining improvable central requirements:"

# ATS readiness measures what the CV can reasonably make visible. General
# behaviours and application-form constraints remain in the strategy/review,
# but they must not force keyword stuffing or repeated CV rewrites.
_CV_ATS_REQUIREMENT_KINDS = {
    "technical_skill",
    "professional_skill",
    "mission",
    "experience",
    "education",
    "domain",
}
_COVERAGE_RANK = {"missing": 0, "indirect": 1, "semantic": 2, "exact": 3}

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

_TRANSFERABLE_EXACT_TERM_COVERAGE = {
    "verified": "semantic",
    "transferable": "semantic",
    "prepared": "indirect",
    "unsupported": "missing",
}


def requirement_counts_toward_ats(requirement: OfferRequirement) -> bool:
    """Return whether a requirement contributes to the CV-readiness score."""

    return requirement.kind in _CV_ATS_REQUIREMENT_KINDS


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

    scored_requirements = [
        requirement for requirement in requirements if requirement_counts_toward_ats(requirement)
    ]

    for priority in ("must", "important", "nice"):
        group_total = 0.0
        group_earned = 0.0
        for requirement in scored_requirements:
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
        coverage_by_id[requirement.requirement_id] != "exact"
        and requirement.requirement_id in improvable_ids
        for requirement in scored_requirements
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
        requirements_by_id = {
            requirement.requirement_id: requirement for requirement in brief.requirements
        }
        material_gaps = [
            f"{requirement_id}: {requirements_by_id[requirement_id].requirement}"
            for requirement_id in breakdown.weak_central_requirement_ids
            if requirement_id in requirements_by_id
        ][:20]
        if not material_gaps:
            material_gaps = list(breakdown.decision_reasons)[:20]
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
    block_on_improvable_gap: bool = True,
) -> CandidateApplicationReview:
    baseline = brief.baseline_cv_assessment
    coverage = normalize_requirement_coverage(
        brief.requirements,
        review.requirement_coverage,
        document_text,
        require_excerpts=require_excerpts,
    )
    coverage = _normalize_exact_term_transferability(brief, coverage)
    improvable_ids = _remaining_improvable_requirement_ids(baseline, coverage)
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
    warnings = list(review.warnings)
    repairs = list(review.repair_actions)
    remaining_central_ids = sorted(
        set(improvable_ids)
        & set(breakdown.critical_requirement_ids + breakdown.weak_central_requirement_ids)
    )
    if review.approved and remaining_central_ids:
        if block_on_improvable_gap:
            blockers.append(RESIDUAL_ATS_GAP_BLOCKER)
            repairs.append(
                CandidateRepairAction(
                    surface="cv",
                    instruction=(
                        "Repair only these remaining improvable central requirements: "
                        f"{', '.join(remaining_central_ids)}. Preserve every accepted CV and "
                        "letter element."
                    ),
                )
            )
        else:
            warnings.append(RESIDUAL_ATS_GAP_WARNING)
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
    data["warnings"] = warnings
    if blockers:
        data.update({"approved": False, "blocking_issues": blockers, "repair_actions": repairs})
    return CandidateApplicationReview.model_validate(data)


def is_residual_ats_gap_only(review: CandidateApplicationReview) -> bool:
    return bool(
        not review.approved
        and review.blocking_issues == [RESIDUAL_ATS_GAP_BLOCKER]
        and len(review.repair_actions) == 1
        and review.repair_actions[0].surface == "cv"
        and review.repair_actions[0].instruction.startswith(_RESIDUAL_ATS_REPAIR_PREFIX)
    )


def accept_residual_ats_gap(review: CandidateApplicationReview) -> CandidateApplicationReview:
    if not is_residual_ats_gap_only(review):
        raise ValueError("review is not a deterministic residual ATS gap")
    warnings = list(review.warnings)
    if RESIDUAL_ATS_GAP_WARNING not in warnings:
        warnings.append(RESIDUAL_ATS_GAP_WARNING)
    return review.model_copy(
        update={
            "approved": True,
            "blocking_issues": [],
            "repair_actions": [],
            "warnings": warnings,
        }
    )


def _normalize_exact_term_transferability(
    brief: ApplicationBrief,
    coverage: list[RenderedRequirementCoverage],
) -> list[RenderedRequirementCoverage]:
    """Stabilize adjacent evidence when the requested literal term is absent.

    Exact lexical presence remains deterministic in ``normalize_requirement_coverage``.
    For a grounded, non-exact excerpt, the canonical evidence assessment decides whether
    the visible alternative is semantic, indirect, or unsupported. This prevents two
    reviewers from assigning different ATS credit to the same transferable evidence.
    """
    requirements = {item.requirement_id: item for item in brief.requirements}
    evidence = {item.requirement_id: item for item in brief.evidence_mappings}
    normalized: list[RenderedRequirementCoverage] = []
    for item in coverage:
        requirement = requirements[item.requirement_id]
        mapping = evidence[item.requirement_id]
        if requirement.matching_mode != "exact_term" or item.coverage in {"exact", "missing"}:
            normalized.append(item)
            continue
        resolved = _TRANSFERABLE_EXACT_TERM_COVERAGE[mapping.evidence_level]
        normalized.append(
            item.model_copy(
                update={
                    "coverage": resolved,
                    "placements": item.placements if resolved != "missing" else [],
                    "supporting_excerpts": (
                        item.supporting_excerpts if resolved != "missing" else []
                    ),
                }
            )
        )
    return normalized


def _remaining_improvable_requirement_ids(
    baseline: BaselineCvAssessment | None,
    final_coverage: list[RenderedRequirementCoverage],
) -> list[str]:
    """Keep repair targets only when the tailored CV made no visible progress."""
    if baseline is None:
        return []
    baseline_states = {item.requirement_id: item.coverage for item in baseline.requirement_coverage}
    final_states = {item.requirement_id: item.coverage for item in final_coverage}
    return [
        requirement_id
        for requirement_id in baseline.improvable_requirement_ids
        if requirement_id in final_states
        and _COVERAGE_RANK[final_states[requirement_id]]
        <= _COVERAGE_RANK.get(baseline_states.get(requirement_id, "missing"), 0)
    ]


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
