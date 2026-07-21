from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import jobauto.candidate_pipeline as candidate_pipeline_module
from jobauto.candidate_context import CandidateContext
from jobauto.candidate_pipeline import (
    CandidatePipeline,
    _application_brief_field_schemas,
    _application_brief_repair_view,
    _brief_repair_requires_full_offer,
    _letter_argument_excerpts_are_grounded,
)
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.codex_client import GenerationPhase
from jobauto.document_patch import CvAdaptationPatch, CvFieldChange
from jobauto.document_renderer import DocumentRenderer
from jobauto.models import (
    AdaptationDecision,
    ApplicationBrief,
    ApplicationBriefReview,
    ApplicationRow,
    BaselineCvAssessment,
    BriefContractViolation,
    BriefRepairAction,
    BriefRequirementAssessment,
    CandidateApplicationReview,
    CandidateLetterDraft,
    CandidateRepairAction,
    EvidenceMapping,
    LetterArgumentAssessment,
    LetterArgumentCriterionAssessment,
    LetterArgumentState,
    OfferRequirement,
    ProjectPlan,
    ProjectSlotPlan,
    RenderedRequirementCoverage,
    RoleFamily,
    SkillPlan,
    SkillPlanItem,
    prewrite_fit_gaps,
    validate_application_brief_contract,
)


def _letter_argument(
    *,
    target_specificity: LetterArgumentState = "pass",
    evidence_to_missions: LetterArgumentState = "pass",
    candidate_contribution: LetterArgumentState = "pass",
    motivation_credibility: LetterArgumentState = "pass",
    tone_and_naturalness: LetterArgumentState = "pass",
    excerpt: str = "I am applying for the Data Engineer role at GridCo.",
) -> LetterArgumentAssessment:
    def criterion(state: LetterArgumentState) -> LetterArgumentCriterionAssessment:
        return LetterArgumentCriterionAssessment(
            state=state,
            rationale="The rendered letter provides concrete evidence for this criterion.",
            supporting_excerpt=excerpt if state == "pass" else None,
        )

    return LetterArgumentAssessment(
        target_specificity=criterion(target_specificity),
        evidence_to_missions=criterion(evidence_to_missions),
        candidate_contribution=criterion(candidate_contribution),
        motivation_credibility=criterion(motivation_credibility),
        tone_and_naturalness=criterion(tone_and_naturalness),
    )


def test_review_approval_depends_on_blockers_not_an_arbitrary_score_threshold() -> None:
    brief_review = ApplicationBriefReview(
        approved=True,
        score=82,
        blocking_issues=[],
        improvements=[],
        repair_actions=[],
        requirement_audit=[
            BriefRequirementAssessment(
                requirement_id="req.example",
                state="pass",
                rationale="The evidence and visible strategy are coherent for this requirement.",
            )
        ],
    )
    document_review = CandidateApplicationReview(
        approved=True,
        score=84,
        ats_score=82,
        editorial_score=86,
        adaptation_score=84,
        blocking_issues=[],
        warnings=[],
        letter_argument=_letter_argument(),
        requirement_coverage=[],
        repair_actions=[],
    )

    assert brief_review.approved is True
    assert document_review.approved is True


def test_approved_review_requires_a_complete_letter_argument() -> None:
    with pytest.raises(ValueError, match="complete letter argument"):
        CandidateApplicationReview(
            approved=True,
            score=94,
            ats_score=93,
            editorial_score=94,
            adaptation_score=92,
            blocking_issues=[],
            warnings=[],
            letter_argument=_letter_argument(target_specificity="repair"),
            requirement_coverage=[],
        )


def test_passed_letter_criterion_requires_a_supporting_excerpt() -> None:
    with pytest.raises(ValueError, match="supporting excerpt"):
        LetterArgumentCriterionAssessment(
            state="pass",
            rationale="The reviewer claims that the criterion is visibly satisfied.",
        )


def test_letter_argument_grounding_tolerates_pdf_line_hyphenation() -> None:
    assessment = _letter_argument(excerpt="European medical-device portfolio")

    assert _letter_argument_excerpts_are_grounded(
        assessment,
        "European medical-\ndevice portfolio",
    )
    assert not _letter_argument_excerpts_are_grounded(
        assessment,
        "Unrelated regulatory text",
    )


def test_letter_argument_gap_requires_a_letter_repair_action() -> None:
    with pytest.raises(ValueError, match="letter repair action"):
        CandidateApplicationReview(
            approved=False,
            score=78,
            ats_score=90,
            editorial_score=72,
            adaptation_score=84,
            blocking_issues=["The letter does not explain why the target role is relevant."],
            warnings=[],
            letter_argument=_letter_argument(target_specificity="repair"),
            requirement_coverage=[],
            repair_actions=[
                CandidateRepairAction(
                    surface="cv",
                    instruction="Keep the CV unchanged while resolving an unrelated CV issue.",
                )
            ],
        )


def test_semantic_brief_repair_expands_coupled_evidence_fields() -> None:
    actions = [
        BriefRepairAction(
            field="skill_plan",
            problem="Visible skills contradict the reviewed evidence levels.",
            instruction="Align visible skills with the evidence contract.",
        )
    ]

    expanded = CandidatePipeline._expand_semantic_brief_repair_actions(actions)

    assert {action.field for action in expanded} == {
        "skill_plan",
        "evidence_mappings",
        "baseline_cv_assessment",
    }
    assert all(
        "skill_plan" not in action.must_preserve and "evidence_mappings" not in action.must_preserve
        for action in expanded
    )


def test_education_requirement_is_not_forced_into_visible_skills() -> None:
    brief = _brief()
    education = OfferRequirement(
        requirement_id="req.education",
        requirement="Hold a relevant master's degree",
        source_excerpt="Master's degree in supply chain or procurement",
        priority="must",
        kind="education",
    )
    brief = brief.model_copy(
        update={
            "requirements": [*brief.requirements, education],
            "evidence_mappings": [
                *brief.evidence_mappings,
                EvidenceMapping(
                    requirement_id="req.education",
                    evidence_level="verified",
                    fact_ids=["profile.summary"],
                    rationale="The candidate profile contains the required degree evidence.",
                ),
            ],
            "baseline_cv_assessment": brief.baseline_cv_assessment.model_copy(
                update={
                    "requirement_coverage": [
                        *brief.baseline_cv_assessment.requirement_coverage,
                        RenderedRequirementCoverage(
                            requirement_id="req.education",
                            coverage="exact",
                            placements=["Education"],
                            rationale="The relevant degree is visible in the source CV.",
                        ),
                    ]
                }
            ),
        }
    )

    validate_application_brief_contract(brief)


def test_brief_contract_violation_routes_only_the_failed_skill_plan() -> None:
    brief = _brief()
    brief = brief.model_copy(
        update={
            "skill_plan": brief.skill_plan.model_copy(
                update={
                    "items": [
                        item
                        for item in brief.skill_plan.items
                        if "req.python" not in item.requirement_ids
                    ]
                }
            )
        }
    )

    with pytest.raises(BriefContractViolation) as caught:
        validate_application_brief_contract(brief)

    actions = CandidatePipeline._brief_validation_repair_actions(
        "semantic_brief_contract",
        str(caught.value),
        exc=caught.value,
    )

    assert caught.value.code == "missing_central_hard_skill_coverage"
    assert [action.field for action in actions] == ["skill_plan"]


def test_targeted_brief_patch_receives_exact_field_value_schemas() -> None:
    schemas = _application_brief_field_schemas(["requirements", "skill_plan"])

    requirement_kind = schemas["requirements"]["$defs"]["OfferRequirement"]["properties"]["kind"]

    assert "education" in requirement_kind["enum"]
    assert "categories" in schemas["skill_plan"]["properties"]
    assert "SkillPlanItem" in schemas["skill_plan"]["$defs"]


def test_targeted_skill_repair_omits_unrelated_brief_and_offer_payload() -> None:
    view = json.loads(_application_brief_repair_view(_brief(), ["skill_plan"]))

    assert "requirements" in view
    assert "evidence_mappings" in view
    assert "skill_plan" in view
    assert "letter_angle" not in view
    assert "company_details" not in view
    assert _brief_repair_requires_full_offer(["skill_plan"]) is False
    assert _brief_repair_requires_full_offer(["requirements"]) is True


def _snapshot():
    project_root = Path(__file__).resolve().parents[1]
    return CandidateProfileRepository(project_root / "config" / "profiles").load_snapshot(
        project_root / "config" / "profiles" / "example" / "profile.yaml"
    )


def _brief() -> ApplicationBrief:
    requirements = [
        OfferRequirement(
            requirement_id="req.python",
            requirement="Build Python data pipelines",
            source_excerpt="build Python and SQL pipelines",
            priority="must",
            kind="technical_skill",
        ),
        OfferRequirement(
            requirement_id="req.energy",
            requirement="Work with operational energy data",
            source_excerpt="for energy data",
            priority="important",
            kind="domain",
        ),
    ]
    return ApplicationBrief(
        company="GridCo",
        role="Data Engineer",
        role_family=RoleFamily.DATA_ENGINEER,
        language="en",
        summary="Build reliable energy data products.",
        responsibilities=["Build data pipelines"],
        required_skills=["Python", "SQL"],
        preferred_skills=[],
        company_details=[],
        seniority="junior",
        normalized_role="Data Engineer",
        targeted_keywords=["Python", "SQL", "data quality"],
        cv_angle="Reliable data engineering for energy operations.",
        letter_angle="Connect data quality work to operational energy decisions.",
        adaptation_guidance=[],
        open_role="Data Engineer",
        sector="Energy",
        specialisations=["data pipelines"],
        requirements=requirements,
        evidence_mappings=[
            EvidenceMapping(
                requirement_id=requirement.requirement_id,
                evidence_level="verified",
                fact_ids=["identity.current"],
                rationale="The verified candidate profile supports this positioning.",
            )
            for requirement in requirements
        ],
        adaptation_decisions=[
            AdaptationDecision(
                surface="both",
                decision="Emphasize reliable pipelines and operational data products.",
                rationale="This is the strongest verified connection to the offer.",
                fact_ids=["identity.current"],
            )
        ],
        project_plan=ProjectPlan(
            decision="create",
            rationale="Use two verified projects and one complementary project slot.",
            central_gaps=["Operational energy data context"],
            slots=[
                ProjectSlotPlan(
                    slot=1,
                    mode="reuse",
                    source_project_id="energy_forecasting",
                    requirement_ids=["req.python"],
                    rationale="The existing project proves a relevant data workflow.",
                ),
                ProjectSlotPlan(
                    slot=2,
                    mode="reuse",
                    source_project_id="mobility_streaming",
                    requirement_ids=["req.python"],
                    rationale="A second verified project demonstrates an operational data platform.",
                ),
                ProjectSlotPlan(
                    slot=3,
                    mode="create",
                    requirement_ids=["req.energy"],
                    rationale="A complementary slot can address the central domain requirement.",
                ),
            ],
        ),
        skill_plan=SkillPlan(
            categories=["Data Engineering", "Cloud", "Analytics"],
            items=[
                SkillPlanItem(
                    name="Python",
                    category="Data Engineering",
                    kind="language",
                    priority="must",
                    evidence_level="verified",
                    requirement_ids=["req.python"],
                ),
                SkillPlanItem(
                    name="BigQuery",
                    category="Cloud",
                    kind="platform",
                    priority="baseline",
                    evidence_level="verified",
                ),
                SkillPlanItem(
                    name="Data quality",
                    category="Analytics",
                    kind="technical_capability",
                    priority="important",
                    evidence_level="transferable",
                    requirement_ids=["req.energy"],
                ),
            ],
            rationale="Keep a concise skills section tied to the central requirements.",
        ),
        baseline_cv_assessment=BaselineCvAssessment(
            decision="adapt",
            ats_score=74,
            confidence="high",
            role_positioning_matches=True,
            language_matches=True,
            material_gaps=["The baseline CV does not expose every central offer signal."],
            improvable_requirement_ids=["req.energy"],
            requirement_coverage=[
                RenderedRequirementCoverage(
                    requirement_id="req.python",
                    coverage="exact",
                    placements=["Skills"],
                    rationale="Python is directly visible in the source CV.",
                ),
                RenderedRequirementCoverage(
                    requirement_id="req.energy",
                    coverage="indirect",
                    placements=["Projects"],
                    rationale="The domain connection can be made materially clearer.",
                ),
            ],
            rationale="The baseline is relevant but one supported central requirement can be made clearer.",
        ),
    )


def test_prewrite_semantic_review_gets_one_targeted_repair_before_final_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BriefLlm:
        def complete_json(self, _prompt, response_model, _phase, **_kwargs):
            assert response_model is ApplicationBrief
            return _brief()

    snapshot = _snapshot()
    pipeline = CandidatePipeline.for_candidate(
        BriefLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
        prewrite_semantic_review=True,
    )
    calls = {"reviews": 0, "repairs": 0}

    def reject_once(brief, *, full_offer, project_lab_context=""):
        del full_offer, project_lab_context
        calls["reviews"] += 1
        return ApplicationBriefReview(
            approved=False,
            score=82,
            blocking_issues=["The strategy needs one coherent evidence correction."],
            improvements=["Correct the summary evidence without reopening accepted fields."],
            repair_actions=[
                BriefRepairAction(
                    field="summary",
                    problem="The summary overstates one part of the available evidence.",
                    instruction="Keep the angle but make the summary fully evidence-grounded.",
                )
            ],
            requirement_audit=[
                BriefRequirementAssessment(
                    requirement_id=requirement.requirement_id,
                    state="repair" if index == 0 else "pass",
                    rationale="The requirement was audited against the candidate evidence.",
                )
                for index, requirement in enumerate(brief.requirements)
            ],
        )

    def repair_once(brief, _actions, **_kwargs):
        calls["repairs"] += 1
        return brief.model_copy(update={"summary": f"{brief.summary} Evidence-grounded."})

    monkeypatch.setattr(
        candidate_pipeline_module,
        "normalize_baseline_assessment",
        lambda brief, _text, require_excerpts: brief,
    )
    monkeypatch.setattr(pipeline, "_validate_lean_brief_fact_ids", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "_review_lean_brief", reject_once)
    monkeypatch.setattr(pipeline, "_repair_lean_brief", repair_once)

    result = pipeline._generate_validated_lean_brief(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data.",
        "",
    )

    assert result.summary.endswith("Evidence-grounded.")
    assert calls == {"reviews": 1, "repairs": 1}


class CandidateWriterLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[type, GenerationPhase]] = []
        self.prompts: list[str] = []

    def complete_json(self, prompt, response_model, phase, **_kwargs):
        self.calls.append((response_model, phase))
        self.prompts.append(prompt)
        if response_model is CvAdaptationPatch:
            return CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="summary.text",
                        value=(
                            "Data engineer focused on reliable energy-data pipelines, data quality "
                            "and analytics products, with hands-on experience translating operational "
                            "requirements into maintainable Python and SQL data flows for business users."
                        ),
                        fact_ids=["identity.current"],
                    )
                ]
            )
        if response_model is CandidateLetterDraft:
            return CandidateLetterDraft(
                greeting="Dear hiring team,",
                paragraphs=[
                    "I am applying for the Data Engineer role at GridCo.",
                    "My experience building analytics pipelines connects directly to the role's focus on reliable operational data products.",
                ],
                closing="Kind regards,\nAlex Morgan",
                used_fact_ids=["identity.current"],
            )
        if response_model is CandidateApplicationReview:
            return CandidateApplicationReview(
                approved=True,
                score=94,
                ats_score=93,
                editorial_score=94,
                adaptation_score=92,
                blocking_issues=[],
                warnings=[],
                letter_argument=_letter_argument(),
                requirement_coverage=[
                    RenderedRequirementCoverage(
                        requirement_id=requirement_id,
                        coverage="exact",
                        placements=["CV"],
                        supporting_excerpts=[
                            "Python" if requirement_id == "req.python" else "energy"
                        ],
                        rationale="The rendered CV contains the required evidence.",
                    )
                    for requirement_id in ("req.python", "req.energy")
                ],
            )
        raise AssertionError(f"Unexpected writer model: {response_model}")


class UngroundedReviewLlm(CandidateWriterLlm):
    def __init__(self) -> None:
        super().__init__()
        self.review_attempts = 0

    def complete_json(self, prompt, response_model, phase, **kwargs):
        result = super().complete_json(prompt, response_model, phase, **kwargs)
        if response_model is CandidateApplicationReview:
            self.review_attempts += 1
            if self.review_attempts == 1:
                return result.model_copy(
                    update={
                        "letter_argument": _letter_argument(
                            excerpt="This excerpt is absent from the rendered letter."
                        )
                    }
                )
        return result


class PolicyRepairLlm(CandidateWriterLlm):
    def __init__(self) -> None:
        super().__init__()
        self.cv_calls = 0

    def complete_json(self, prompt, response_model, phase, **kwargs):
        if response_model is CvAdaptationPatch:
            self.calls.append((response_model, phase))
            self.prompts.append(prompt)
            self.cv_calls += 1
            return CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="summary.text",
                        value=(
                            "Data engineer focused on reliable energy-data pipelines, data quality "
                            "and analytics products, with hands-on experience translating operational "
                            "requirements into maintainable Python and SQL data flows for business users."
                        ),
                        fact_ids=(
                            ["project.energy_forecasting"]
                            if self.cv_calls == 1
                            else ["identity.current"]
                        ),
                    )
                ]
            )
        return super().complete_json(prompt, response_model, phase, **kwargs)


class LetterContractRepairLlm(CandidateWriterLlm):
    def __init__(self) -> None:
        super().__init__()
        self.letter_calls = 0

    def complete_json(self, prompt, response_model, phase, **kwargs):
        if response_model is CandidateLetterDraft:
            self.calls.append((response_model, phase))
            self.prompts.append(prompt)
            self.letter_calls += 1
            return CandidateLetterDraft(
                greeting="Dear hiring team,",
                paragraphs=["I am applying based on relevant data-engineering experience."],
                closing=(
                    "Kind regards,\nForeign Person"
                    if self.letter_calls == 1
                    else "Kind regards,\nAlex Morgan"
                ),
                used_fact_ids=["identity.current"],
            )
        return super().complete_json(prompt, response_model, phase, **kwargs)


def test_candidate_writers_return_snapshot_bound_patch_letter_and_review(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    context = CandidateContext.from_snapshot(snapshot)
    llm = CandidateWriterLlm()
    pipeline = CandidatePipeline.for_candidate(llm, snapshot, context)

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data.",
        brief=_brief(),
        project_lab_context="visible_cv_project_ids: energy_forecasting",
    )
    renderer = DocumentRenderer()
    cv_rendered = renderer.render_cv(snapshot, package.cv, tmp_path / "cv")
    letter_rendered = renderer.render_letter(snapshot, package.letter, tmp_path / "letter")
    review = pipeline.review_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        package,
        cv_rendered,
        letter_rendered,
        "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data.",
    )

    assert package.cv.document.summary.startswith("Data engineer focused on reliable energy")
    assert package.cv.provenance == {"summary.text": ("identity.current",)}
    assert package.letter.closing.endswith("Alex Morgan")
    assert review.approved is True
    assert [model for model, _phase in llm.calls] == [
        CvAdaptationPatch,
        CandidateLetterDraft,
        CandidateApplicationReview,
    ]
    prompts = "\n".join(llm.prompts).casefold()
    assert context.context_hash in prompts
    assert "summary.text" in prompts
    assert "projects.section" in prompts
    assert "skills.section" in prompts
    assert "visible_cv_project_ids: energy_forecasting" in prompts
    assert "education.0.title" not in llm.prompts[0].casefold()
    assert "southwest institute of technology" in llm.prompts[-1].casefold()
    assert "english c1" in llm.prompts[-1].casefold()
    assert "privatecandidate" not in prompts
    assert "privateemployer" not in prompts
    assert cv_rendered.pdf_sha256 in prompts
    assert letter_rendered.pdf_sha256 in prompts
    assert '"vertical_coverage_ratio"' in llm.prompts[-1]
    assert "largest font and spacing that fit one page" in llm.prompts[-1]
    assert "requires_density_review" in llm.prompts[-1]
    assert "never request invented filler" in llm.prompts[-1].casefold()
    assert "do not impose a paragraph template, word count or page-fill target" in prompts
    assert "target_specificity" in prompts
    assert "candidate_contribution" in prompts
    assert "motivation_credibility" in prompts
    assert "tone_and_naturalness" in prompts
    assert "supporting_excerpt" in prompts
    assert "would welcome the opportunity does not pass" in prompts
    assert "do not add filler or lengthen the letter merely to fill the page" in prompts
    assert "do not use an internal project title that has no meaning" in prompts
    assert "missing or unsupported offer requirement" in prompts
    assert "never reject the package solely because it is absent" in prompts


def test_strategy_prompt_keeps_unsupported_requirements_as_fit_warnings() -> None:
    snapshot = _snapshot()
    pipeline = CandidatePipeline.for_candidate(
        CandidateWriterLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    prompt = pipeline._application_strategy_prompt(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo requires Python and a proprietary platform.",
    ).casefold()

    assert "unsupported requirement remains a fit warning" in prompt
    assert "must not terminate document generation" in prompt


def test_candidate_pipeline_keeps_a_strong_baseline_and_skips_cv_writers(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    context = CandidateContext.from_snapshot(snapshot)
    llm = CandidateWriterLlm()
    pipeline = CandidatePipeline.for_candidate(llm, snapshot, context)
    brief = _brief()
    brief = brief.model_copy(
        update={
            "baseline_cv_assessment": BaselineCvAssessment(
                decision="keep_baseline",
                ats_score=96,
                confidence="high",
                role_positioning_matches=True,
                language_matches=True,
                material_gaps=[],
                improvable_requirement_ids=[],
                requirement_coverage=[
                    RenderedRequirementCoverage(
                        requirement_id=requirement.requirement_id,
                        coverage="exact",
                        placements=["Source CV"],
                        rationale="The source CV already makes this requirement directly visible.",
                    )
                    for requirement in brief.requirements
                ],
                rationale="The source CV already covers every central requirement and needs no material rewrite.",
            )
        }
    )

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data.",
        brief=brief,
    )

    assert package.cv_patch == CvAdaptationPatch()
    assert package.cv.document == snapshot.cv_source
    assert not package.cv.provenance
    assert GenerationPhase.CV_WRITER not in [phase for _model, phase in llm.calls]
    assert GenerationPhase.CV_LATEX_WRITER not in [phase for _model, phase in llm.calls]
    rendered = DocumentRenderer().render_cv(snapshot, package.cv, tmp_path / "baseline")
    assert rendered.page_count == 1


def test_brief_contract_rejects_keep_when_supported_central_coverage_is_weak() -> None:
    brief = _brief()
    assessment = BaselineCvAssessment(
        decision="keep_baseline",
        ats_score=91,
        confidence="high",
        role_positioning_matches=True,
        language_matches=True,
        material_gaps=[],
        improvable_requirement_ids=[],
        requirement_coverage=[
            RenderedRequirementCoverage(
                requirement_id=requirement.requirement_id,
                coverage="indirect" if requirement.requirement_id == "req.energy" else "exact",
                placements=["Source CV"],
                rationale="The source CV contains this signal with the stated strength.",
            )
            for requirement in brief.requirements
        ],
        rationale="This intentionally optimistic assessment exercises the deterministic gate.",
    )

    with pytest.raises(BriefContractViolation, match="weak coverage"):
        validate_application_brief_contract(
            brief.model_copy(update={"baseline_cv_assessment": assessment})
        )


def test_candidate_review_retries_an_ungrounded_letter_excerpt() -> None:
    snapshot = _snapshot()
    llm = UngroundedReviewLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    row = ApplicationRow(
        excel_row=1,
        company="GridCo",
        role="Data Engineer",
        url="https://example.test/jobs/data-engineer",
    )
    offer = "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data."
    package = pipeline.generate_candidate_documents(row, offer, brief=_brief())
    cv_rendered = SimpleNamespace(
        pdf_path=Path("cv.pdf"),
        pdf_sha256="a" * 64,
        extracted_text_sha256="b" * 64,
        layout_metrics={},
        extracted_text="Python pipelines for energy data.",
    )
    letter_rendered = SimpleNamespace(
        pdf_path=Path("letter.pdf"),
        pdf_sha256="c" * 64,
        extracted_text_sha256="d" * 64,
        layout_metrics={},
        extracted_text=(
            "I am applying for the Data Engineer role at GridCo. My experience building "
            "analytics pipelines connects directly to reliable operational data products."
        ),
    )

    review = pipeline.review_candidate_documents(
        row,
        package,
        cv_rendered,
        letter_rendered,
        offer,
    )

    assert review.approved is True
    assert llm.review_attempts == 2
    assert "INVALID STRUCTURED REVIEW" in llm.prompts[-1]


def test_candidate_writer_repairs_a_structured_policy_violation_before_rendering() -> None:
    snapshot = _snapshot()
    llm = PolicyRepairLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data.",
        brief=_brief(),
    )

    assert package.cv.provenance["summary.text"] == ("identity.current",)
    assert [phase for model, phase in llm.calls if model is CvAdaptationPatch] == [
        GenerationPhase.CV_WRITER,
        GenerationPhase.REPAIR,
    ]
    assert "contract validation failure" in llm.prompts[1].casefold()
    assert "protected_fact_missing" in llm.prompts[1]


def test_candidate_writer_repairs_a_letter_contract_failure_before_rendering() -> None:
    snapshot = _snapshot()
    llm = LetterContractRepairLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data.",
        brief=_brief(),
    )

    assert package.letter.closing.endswith("Alex Morgan")
    assert [phase for model, phase in llm.calls if model is CandidateLetterDraft] == [
        GenerationPhase.LETTER_WRITER,
        GenerationPhase.REPAIR,
    ]
    assert "letter contract validation failure" in llm.prompts[-1].casefold()
    assert "letter signature does not match candidate identity" in llm.prompts[-1].casefold()


def test_candidate_generation_continues_with_unsupported_mandatory_experience() -> None:
    snapshot = _snapshot()
    terminal = _brief().model_copy(deep=True)
    terminal.requirements[0] = terminal.requirements[0].model_copy(
        update={"kind": "experience", "priority": "must"}
    )
    terminal.evidence_mappings[0] = terminal.evidence_mappings[0].model_copy(
        update={
            "evidence_level": "unsupported",
            "fact_ids": [],
            "rationale": "The mandatory experience requirement is not covered.",
        }
    )
    llm = CandidateWriterLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    offer = "\n".join(requirement.source_excerpt for requirement in terminal.requirements)

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        offer,
        brief=terminal,
    )

    assert prewrite_fit_gaps(package.brief)[0]["requirement_id"] == "req.python"
    assert [phase for _model, phase in llm.calls] == [
        GenerationPhase.CV_WRITER,
        GenerationPhase.LETTER_WRITER,
    ]


def test_candidate_generation_continues_with_unsupported_must_skill() -> None:
    snapshot = _snapshot()
    terminal = _brief().model_copy(deep=True)
    terminal.requirements[0] = terminal.requirements[0].model_copy(
        update={"kind": "technical_skill", "priority": "must"}
    )
    terminal.evidence_mappings[0] = terminal.evidence_mappings[0].model_copy(
        update={
            "evidence_level": "unsupported",
            "fact_ids": [],
            "rationale": "The mandatory technical capability is not defensible from the profile.",
        }
    )
    llm = CandidateWriterLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    offer = "\n".join(requirement.source_excerpt for requirement in terminal.requirements)

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        offer,
        brief=terminal,
    )

    assert prewrite_fit_gaps(package.brief)[0]["requirement_id"] == "req.python"
    assert [phase for _model, phase in llm.calls] == [
        GenerationPhase.CV_WRITER,
        GenerationPhase.LETTER_WRITER,
    ]


def test_candidate_generation_does_not_treat_future_mission_as_terminal_gap() -> None:
    brief = _brief().model_copy(deep=True)
    brief.requirements[0] = brief.requirements[0].model_copy(
        update={"kind": "mission", "priority": "must"}
    )
    brief.evidence_mappings[0] = brief.evidence_mappings[0].model_copy(
        update={
            "evidence_level": "unsupported",
            "fact_ids": [],
            "rationale": "This responsibility will be exercised in the target role.",
        }
    )

    assert prewrite_fit_gaps(brief) == []


def test_candidate_letter_rejects_foreign_signature() -> None:
    letter = CandidateLetterDraft(
        greeting="Dear hiring team,",
        paragraphs=["I am applying for this role based on relevant data-engineering experience."],
        closing="Kind regards,\nForeign Person",
        used_fact_ids=["identity.current"],
    )

    with pytest.raises(ValueError, match="signature"):
        letter.validate_for_snapshot(_snapshot())


def test_candidate_repair_uses_structured_review_actions() -> None:
    snapshot = _snapshot()
    llm = CandidateWriterLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm, snapshot, CandidateContext.from_snapshot(snapshot)
    )
    row = ApplicationRow(
        excel_row=1,
        company="GridCo",
        role="Data Engineer",
        url="https://example.test/jobs/data-engineer",
    )
    offer = "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data."
    package = pipeline.generate_candidate_documents(row, offer, brief=_brief())
    rejected = CandidateApplicationReview(
        approved=False,
        score=74,
        ats_score=70,
        editorial_score=86,
        adaptation_score=72,
        blocking_issues=["The central data-quality requirement is not visible enough."],
        warnings=[],
        letter_argument=_letter_argument(),
        requirement_coverage=[],
        repair_actions=[
            CandidateRepairAction(
                surface="both",
                instruction="Strengthen the verified data-quality evidence in both documents.",
            )
        ],
    )

    repaired = pipeline.repair_candidate_documents(
        row,
        package,
        rejected,
        offer,
        project_lab_context="visible_cv_project_ids: energy_forecasting",
    )

    assert repaired.cv.document.summary.startswith("Data engineer focused on reliable energy")
    assert repaired.letter.closing.endswith("Alex Morgan")
    assert [phase for _model, phase in llm.calls[-2:]] == [
        GenerationPhase.REPAIR,
        GenerationPhase.REPAIR,
    ]
    assert "current_patch" in llm.prompts[-2]
    assert "visible_cv_project_ids: energy_forecasting" in llm.prompts[-2]
    assert "current_letter" in llm.prompts[-1]
    assert "visible_cv_project_ids: energy_forecasting" in llm.prompts[-1]


def test_candidate_repair_can_target_only_the_letter_argument() -> None:
    snapshot = _snapshot()
    llm = CandidateWriterLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm, snapshot, CandidateContext.from_snapshot(snapshot)
    )
    row = ApplicationRow(
        excel_row=1,
        company="GridCo",
        role="Data Engineer",
        url="https://example.test/jobs/data-engineer",
    )
    offer = "GridCo seeks a Data Engineer to build Python and SQL pipelines for energy data."
    package = pipeline.generate_candidate_documents(row, offer, brief=_brief())
    calls_before_repair = len(llm.calls)
    rejected = CandidateApplicationReview(
        approved=False,
        score=78,
        ats_score=90,
        editorial_score=72,
        adaptation_score=86,
        blocking_issues=["The letter does not explain credible interest in the target scope."],
        warnings=[],
        letter_argument=_letter_argument(motivation_credibility="repair"),
        requirement_coverage=[],
        repair_actions=[
            CandidateRepairAction(
                surface="letter",
                instruction=(
                    "Replace generic motivation with a sourced reason tied to the target scope."
                ),
            )
        ],
    )

    repaired = pipeline.repair_candidate_documents(row, package, rejected, offer)

    assert repaired.cv == package.cv
    assert repaired.letter.closing.endswith("Alex Morgan")
    assert llm.calls[calls_before_repair:] == [(CandidateLetterDraft, GenerationPhase.REPAIR)]
    assert "generic motivation" in llm.prompts[-1].casefold()


def test_candidate_letter_rejects_unverified_or_unknown_fact() -> None:
    letter = CandidateLetterDraft(
        greeting="Dear hiring team,",
        paragraphs=["I am applying for this role based on relevant data-engineering experience."],
        closing="Kind regards,\nAlex Morgan",
        used_fact_ids=["foreign.fact"],
    )

    with pytest.raises(KeyError, match="Unknown candidate fact"):
        letter.validate_for_snapshot(_snapshot())


def test_generic_brief_validation_accepts_candidate_project_evidence_and_compact_groups() -> None:
    snapshot = _snapshot()
    pipeline = CandidatePipeline.for_candidate(
        CandidateWriterLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    brief = _brief().model_copy(deep=True)
    brief.evidence_mappings[0].fact_ids = ["project.energy_forecasting"]

    pipeline._validate_lean_brief_fact_ids(brief)


def test_generic_brief_requires_a_persisted_source_before_external_project_inspiration() -> None:
    snapshot = _snapshot()
    pipeline = CandidatePipeline.for_candidate(
        CandidateWriterLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    brief = _brief().model_copy(deep=True)
    brief.project_plan.slots[1].requires_external_inspiration = True

    with pytest.raises(ValueError, match="resolved external inspiration"):
        pipeline._validate_lean_brief_fact_ids(brief, project_lab_context="")

    pipeline._validate_lean_brief_fact_ids(
        brief,
        project_lab_context=(
            "### External Inspirations\n"
            "- Relevant repository: https://github.com/example/energy-forecasting"
        ),
    )
