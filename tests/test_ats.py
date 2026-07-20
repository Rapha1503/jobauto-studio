from jobauto.ats import (
    ATS_READY_SCORE,
    calculate_ats_readiness,
    normalize_baseline_assessment,
    normalize_requirement_coverage,
)
from jobauto.models import (
    AdaptationDecision,
    ApplicationBrief,
    BaselineCvAssessment,
    EvidenceMapping,
    OfferRequirement,
    ProjectPlan,
    RenderedRequirementCoverage,
    RoleFamily,
    SkillPlan,
    SkillPlanItem,
)


def _requirement(
    requirement_id: str,
    *,
    priority: str,
    matching_mode: str,
    ats_terms: list[str] | None = None,
) -> OfferRequirement:
    return OfferRequirement(
        requirement_id=requirement_id,
        requirement=f"Requirement {requirement_id}",
        source_excerpt=f"Offer excerpt for {requirement_id}",
        priority=priority,
        matching_mode=matching_mode,
        ats_terms=ats_terms or [],
        kind="technical_skill" if matching_mode == "exact_term" else "mission",
    )


def _brief(
    requirements: list[OfferRequirement],
    assessment: BaselineCvAssessment,
) -> ApplicationBrief:
    return ApplicationBrief(
        company="Example",
        role="Analyst",
        role_family=RoleFamily.DEFAULT,
        language="en",
        summary="Analyze operational data.",
        responsibilities=["Analyze data"],
        required_skills=[],
        preferred_skills=[],
        company_details=[],
        seniority="unspecified",
        normalized_role="Analyst",
        targeted_keywords=[],
        cv_angle="Evidence-led analysis.",
        letter_angle="Connect verified evidence to the role.",
        adaptation_guidance=[],
        open_role="Analyst",
        sector="Services",
        requirements=requirements,
        evidence_mappings=[
            EvidenceMapping(
                requirement_id=requirement.requirement_id,
                evidence_level="transferable",
                rationale="The candidate has adjacent evidence that can support this requirement.",
            )
            for requirement in requirements
        ],
        adaptation_decisions=[
            AdaptationDecision(
                surface="both",
                decision="Use the strongest relevant evidence.",
                rationale="The evidence should remain concise and role-specific.",
            )
        ],
        project_plan=ProjectPlan(
            decision="none",
            rationale="This profile does not use a project section for this application.",
        ),
        skill_plan=SkillPlan(
            categories=["Analysis"],
            items=[
                SkillPlanItem(
                    name="Analysis",
                    category="Analysis",
                    kind="professional_method",
                    priority="baseline",
                    evidence_level="transferable",
                )
            ],
            rationale="Keep one broad and relevant competency category.",
        ),
        baseline_cv_assessment=assessment,
    )


def test_exact_term_coverage_is_grounded_in_the_real_cv() -> None:
    requirement = _requirement(
        "req.python",
        priority="must",
        matching_mode="exact_term",
        ats_terms=["Python"],
    )
    normalized = normalize_requirement_coverage(
        [requirement],
        [
            RenderedRequirementCoverage(
                requirement_id="req.python",
                coverage="missing",
                rationale="The evaluator missed the literal term.",
            )
        ],
        "Skills: SQL, Python, Power BI",
        require_excerpts=True,
    )

    assert normalized[0].coverage == "exact"
    assert normalized[0].supporting_excerpts == ["Python"]


def test_single_character_exact_term_does_not_match_inside_other_words() -> None:
    requirement = _requirement(
        "req.r",
        priority="must",
        matching_mode="exact_term",
        ats_terms=["R"],
    )

    normalized = normalize_requirement_coverage(
        [requirement],
        [
            RenderedRequirementCoverage(
                requirement_id="req.r",
                coverage="missing",
                rationale="The R language is not visible.",
            )
        ],
        "Risk reporting and forecasting",
        require_excerpts=True,
    )

    assert normalized[0].coverage == "missing"


def test_readiness_weights_requirements_and_exposes_critical_gaps() -> None:
    requirements = [
        _requirement(
            "req.dataiku",
            priority="must",
            matching_mode="exact_term",
            ats_terms=["Dataiku"],
        ),
        _requirement(
            "req.analysis",
            priority="important",
            matching_mode="semantic_concept",
        ),
    ]
    coverage = [
        RenderedRequirementCoverage(
            requirement_id="req.dataiku",
            coverage="missing",
            rationale="Dataiku is not visible.",
        ),
        RenderedRequirementCoverage(
            requirement_id="req.analysis",
            coverage="semantic",
            supporting_excerpts=["analyzed operational trends"],
            rationale="The CV demonstrates the same analytical responsibility.",
        ),
    ]

    result = calculate_ats_readiness(
        requirements,
        coverage,
        parseable=True,
        role_positioning_matches=True,
        language_matches=True,
        improvable_requirement_ids=["req.dataiku"],
    )

    assert result.score < ATS_READY_SCORE
    assert result.critical_requirement_ids == ["req.dataiku"]
    assert result.adaptation_recommended is True
    assert result.ready_without_cv_changes is False


def test_high_grounded_baseline_skips_cosmetic_cv_rewriting() -> None:
    requirements = [
        _requirement(
            "req.python",
            priority="must",
            matching_mode="exact_term",
            ats_terms=["Python"],
        ),
        _requirement(
            "req.analysis",
            priority="important",
            matching_mode="semantic_concept",
        ),
    ]
    assessment = BaselineCvAssessment(
        decision="adapt",
        ats_score=12,
        confidence="medium",
        role_positioning_matches=True,
        language_matches=True,
        material_gaps=["An arbitrary model score requested a rewrite."],
        improvable_requirement_ids=[],
        requirement_coverage=[
            RenderedRequirementCoverage(
                requirement_id="req.python",
                coverage="exact",
                supporting_excerpts=["Python"],
                rationale="Python is directly visible.",
            ),
            RenderedRequirementCoverage(
                requirement_id="req.analysis",
                coverage="semantic",
                supporting_excerpts=["analyzed operational trends"],
                rationale="The CV directly demonstrates the analytical mission.",
            ),
        ],
        rationale="This deliberately inconsistent assessment must be normalized by JobAuto.",
    )

    normalized = normalize_baseline_assessment(
        _brief(requirements, assessment),
        "Python; analyzed operational trends",
    ).baseline_cv_assessment

    assert normalized is not None
    assert normalized.ats_score >= ATS_READY_SCORE
    assert normalized.decision == "keep_baseline"
    assert normalized.ats_breakdown is not None
    assert normalized.ats_breakdown.ready_without_cv_changes is True


def test_low_baseline_is_adapted_only_when_the_gap_is_improvable() -> None:
    requirement = _requirement(
        "req.dataiku",
        priority="must",
        matching_mode="exact_term",
        ats_terms=["Dataiku"],
    )
    assessment = BaselineCvAssessment(
        decision="adapt",
        ats_score=99,
        confidence="high",
        role_positioning_matches=True,
        language_matches=True,
        material_gaps=["The named platform is not visible."],
        improvable_requirement_ids=["req.dataiku"],
        requirement_coverage=[
            RenderedRequirementCoverage(
                requirement_id="req.dataiku",
                coverage="indirect",
                supporting_excerpts=["analytics platform"],
                rationale="Only adjacent platform experience is visible.",
            )
        ],
        rationale="The free model score must not override the requirement evidence.",
    )

    normalized = normalize_baseline_assessment(
        _brief([requirement], assessment),
        "Experience with an analytics platform",
    ).baseline_cv_assessment

    assert normalized is not None
    assert normalized.ats_score < ATS_READY_SCORE
    assert normalized.decision == "adapt"
    assert normalized.improvable_requirement_ids == ["req.dataiku"]
