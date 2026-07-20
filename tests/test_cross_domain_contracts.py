from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jobauto.candidate_draft import CandidateDraft
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.candidate_workflow import CandidateWorkflowPipeline
from jobauto.document_patch import CvAdaptationPatch, apply_cv_patch
from jobauto.document_renderer import DocumentRenderer
from jobauto.latex_cv_source import analyze_latex_cv
from jobauto.models import (
    ApplicationBrief,
    ApplicationRow,
    JobProfile,
    OfferAnalysis,
    ProjectPlan,
    ProjectSlotPlan,
    SkillPlan,
    SkillPlanItem,
)
from jobauto.project_bank import (
    ProjectBank,
    ProjectBankEntry,
    ProjectStatus,
    ProjectVisibility,
)
from jobauto.project_lab import (
    GithubInspirationProvider,
    ProjectLabCandidate,
    ProjectLabFamily,
    ProjectLabNeedFrame,
    ProjectLabReport,
    ProjectLabScores,
    ProjectLabService,
    _github_search_queries,
)
from jobauto.project_lab_policy import ProjectLabPolicy
from jobauto.skills import SkillPolicy

PROFILE_FIXTURES = Path(__file__).parent / "fixtures" / "cross_domain_profiles"


def test_offer_analysis_never_keeps_schema_role_placeholders() -> None:
    offer = OfferAnalysis(
        company="Studio North",
        role="Exhibition Producer",
        role_family="Default",
        normalized_role="Target role",
        language="en",
        summary="Produce touring exhibitions.",
        responsibilities=["Coordinate venues and suppliers"],
        required_skills=["Production planning"],
    )

    assert offer.normalized_role == "Exhibition Producer"
    assert offer.role_family == "Exhibition Producer"


def test_free_form_occupation_and_non_it_skills_are_valid() -> None:
    offer = OfferAnalysis(
        company="Medica Europe",
        role="Regulatory Affairs Specialist",
        role_family="Regulatory Affairs",
        language="en",
        summary="Prepare compliant medical-device submissions.",
        responsibilities=["Maintain technical documentation"],
        required_skills=["EU MDR", "ISO 13485"],
        normalized_role="Regulatory Affairs Specialist",
    )
    skills = SkillPlan(
        categories=["Regulatory expertise", "Quality systems"],
        items=[
            SkillPlanItem(
                name="EU MDR",
                category="Regulatory expertise",
                kind="domain_knowledge",
                priority="must",
                evidence_level="verified",
            ),
            SkillPlanItem(
                name="ISO 13485",
                category="Quality systems",
                kind="standard",
                priority="important",
                evidence_level="verified",
            ),
        ],
        rationale="Expose the occupation's verified hard skills without an IT taxonomy.",
    )

    assert offer.role_family == "Regulatory Affairs"
    assert [item.kind for item in skills.items] == ["domain_knowledge", "standard"]


def test_free_form_role_schema_and_defaults_do_not_anchor_candidate_domain() -> None:
    profile = JobProfile(
        company="Front Desk Co",
        role="Receptionist",
        role_family="Receptionist",
        summary="Welcome visitors and coordinate the front desk.",
        responsibilities=["Welcome visitors"],
        required_skills=["Reception"],
    )

    schema = JobProfile.model_json_schema()
    brief_schema = ApplicationBrief.model_json_schema()

    assert profile.seniority == "unspecified"
    assert schema["properties"]["role_family"]["type"] == "string"
    assert "$defs" not in schema or "RoleFamily" not in schema["$defs"]
    assert "RoleFamily" not in brief_schema.get("$defs", {})
    assert "specialisations" not in brief_schema["required"]


def test_project_bank_does_not_force_irrelevant_projects_or_ignore_limit() -> None:
    offer = OfferAnalysis(
        company="Museum Network",
        role="Exhibition Producer",
        role_family="Exhibition Producer",
        language="en",
        summary="Produce touring exhibitions.",
        responsibilities=["Coordinate venues and suppliers"],
        required_skills=["Production planning"],
    )
    bank = ProjectBank(
        [
            ProjectBankEntry(
                id="data_rag",
                title="RAG assistant",
                status=ProjectStatus.VERIFIED_PUBLIC,
                visibility=ProjectVisibility.CV_PROJECT,
                role_fit=["Data Engineer"],
                keywords=["Python"],
            ),
            ProjectBankEntry(
                id="ml_model",
                title="ML classifier",
                status=ProjectStatus.VERIFIED_PUBLIC,
                visibility=ProjectVisibility.CV_PROJECT,
                role_fit=["Data Scientist"],
                keywords=["PyTorch"],
            ),
        ]
    )

    selection = bank.select(
        offer,
        "Coordinate touring exhibitions, venues and suppliers.",
        cv_limit=1,
    )

    assert selection.cv_projects == []


def test_skill_categories_are_not_aliased_by_shared_domain_words() -> None:
    policy = SkillPolicy(
        verified={"Machine operation": ["Lockout procedures"]},
        transferable={},
        minimum_group_overlap=0.0,
    )

    assert policy.canonical_group("Machine safety") == "Machine safety"
    assert policy.canonical_group("machine operation") == "Machine operation"


def test_skill_strategy_is_not_rejected_by_a_global_character_budget() -> None:
    skills = SkillPlan(
        categories=["Regulatory operations"],
        items=[
            SkillPlanItem(
                name=name,
                category="Regulatory operations",
                kind="professional_method",
                priority="important",
                evidence_level="verified",
            )
            for name in (
                "Technical documentation maintenance",
                "Notified-body submission coordination",
                "Post-market surveillance reporting",
            )
        ],
        rationale=("The imported candidate CV, not a product-wide character proxy, owns layout."),
    )

    rendered_line = f"{skills.categories[0]} : " + ", ".join(item.name for item in skills.items)
    assert len(rendered_line) > 118
    assert len(skills.items) == 3


def test_candidate_can_explicitly_have_no_project_section() -> None:
    policy = ProjectLabPolicy(minimum_visible_projects=0, maximum_visible_projects=0)
    plan = ProjectPlan(
        decision="none",
        rationale="This candidate CV does not use a personal-project section.",
        slots=[],
    )

    assert policy.maximum_visible_projects == 0
    assert plan.slots == []

    with pytest.raises(ValueError):
        ProjectSlotPlan(
            slot=1,
            mode="none",
            rationale="A slot cannot represent the absence of a project.",
        )


def test_project_lab_does_not_impose_projects_without_candidate_configuration() -> None:
    policy = ProjectLabPolicy()

    assert policy.minimum_visible_projects == 0
    assert policy.maximum_visible_projects == 0


def test_frontend_project_search_does_not_inject_data_stack() -> None:
    offer = OfferAnalysis(
        company="Interface Studio",
        role="Frontend Software Engineer",
        role_family="Frontend Engineering",
        language="en",
        summary="Build accessible web interfaces.",
        responsibilities=["Ship responsive React applications"],
        required_skills=["TypeScript", "React", "accessibility"],
        preferred_skills=["Playwright"],
        targeted_keywords=["design systems"],
        normalized_role="Frontend Software Engineer",
    )

    queries = _github_search_queries(offer, "React TypeScript WCAG design systems")
    joined = " ".join(queries).casefold()

    assert "frontend software engineer" in joined
    assert "typescript" in joined
    assert "python" not in joined
    assert "data engineering" not in joined


def test_github_inspiration_is_not_forced_on_a_nontechnical_brief() -> None:
    nontechnical_brief = SimpleNamespace(requirements=[SimpleNamespace(kind="professional_skill")])

    assert GithubInspirationProvider().find(nontechnical_brief, "EU MDR submissions") == []


@pytest.mark.parametrize(
    ("domain", "methods_tools_or_materials"),
    [
        ("legal", "doctrinal analysis, regulatory corpus, comparative matrix"),
        ("cultural production", "curatorial brief, installation plan, loan checklist"),
        ("molecular biology", "qPCR, microscopy, experimental protocol"),
        ("operations", "process mapping, service blueprint, stakeholder interviews"),
    ],
)
def test_project_lab_project_contract_is_not_it_specific(
    domain: str,
    methods_tools_or_materials: str,
) -> None:
    candidate = ProjectLabCandidate(
        id="cross_domain_project",
        family=ProjectLabFamily.SYNTHETIC_PROJECT,
        title="Role-relevant applied project",
        target_domain=domain,
        role_fit=[domain],
        methods_tools_or_materials=methods_tools_or_materials,
        bullets=["Produced a role-relevant deliverable from traceable source material."],
        supervisor_scores=ProjectLabScores(
            ats_fit=4,
            execution_coherence=5,
            profile_fit=4,
            recruiter_plausibility=5,
            interview_defensibility=5,
            overfit_risk=1,
        ),
    )

    assert candidate.methods_tools_or_materials == methods_tools_or_materials
    assert "stack_line" not in candidate.model_dump()


def test_project_lab_accepts_legacy_stack_keys_without_exposing_them() -> None:
    need_frame = ProjectLabNeedFrame.model_validate(
        {
            "business_domain": "software",
            "business_problem": "Build a reliable monitored machine-learning workflow.",
            "data_shape": "training records and model evaluation results",
            "users": "machine-learning team",
            "deliverable": "monitored training workflow",
            "stack_rationale": "Use the requested engineering stack to make execution reproducible.",
            "cv_slot_budget": 1,
        }
    )
    candidate = ProjectLabCandidate.model_validate(
        {
            "id": "legacy_it_project",
            "family": "synthetic_project",
            "title": "Monitored ML workflow",
            "target_domain": "software",
            "role_fit": ["ML Engineer"],
            "stack_line": "Python, MLflow, Docker",
            "bullets": ["Built and monitored a reproducible training workflow."],
            "supervisor_scores": {
                "ats_fit": 4,
                "stack_coherence": 5,
                "profile_fit": 4,
                "recruiter_plausibility": 4,
                "interview_defensibility": 4,
                "overfit_risk": 1,
            },
        }
    )

    assert candidate.methods_tools_or_materials == "Python, MLflow, Docker"
    assert candidate.supervisor_scores.execution_coherence == 5
    assert need_frame.inputs_or_materials == "training records and model evaluation results"
    assert "methods_tools_or_materials" in candidate.model_dump()
    assert "execution_coherence" in candidate.supervisor_scores.model_dump()
    assert "stack_line" not in ProjectLabCandidate.model_json_schema()["properties"]


def test_project_lab_preserves_an_explicit_zero_project_budget() -> None:
    snapshot = CandidateProfileRepository(PROFILE_FIXTURES).load_snapshot(
        PROFILE_FIXTURES / "regulatory" / "profile.yaml"
    )

    class ZeroBudgetLlm:
        telemetry_log: list[dict] = []

        def __init__(self) -> None:
            self.prompt = ""

        def complete_json(self, prompt, _model, _phase):
            self.prompt = prompt
            return ProjectLabReport(
                need_frame=ProjectLabNeedFrame(
                    business_domain="medical device regulation",
                    business_problem="Prepare a traceable regulatory submission for market access.",
                    inputs_or_materials="technical file and EU MDR guidance",
                    users="regulatory affairs team",
                    deliverable="submission-ready compliance package",
                    execution_approach_rationale=(
                        "Use document review, requirement mapping and controlled evidence checks."
                    ),
                    cv_slot_budget=0,
                ),
                candidates=[
                    ProjectLabCandidate(
                        id="regulatory_context",
                        family=ProjectLabFamily.SYNTHETIC_PROJECT,
                        title="Regulatory submission planning",
                        target_domain="medical devices",
                        role_fit=["Regulatory Affairs Specialist"],
                        methods_tools_or_materials=(
                            "EU MDR guidance, technical documentation, compliance matrix"
                        ),
                        bullets=["Structured requirements into a traceable submission plan."],
                        supervisor_scores=ProjectLabScores(
                            ats_fit=4,
                            execution_coherence=5,
                            profile_fit=4,
                            recruiter_plausibility=5,
                            interview_defensibility=5,
                            overfit_risk=1,
                        ),
                    )
                ],
                selected_candidate_ids=["regulatory_context"],
                visible_cv_project_ids=[],
                supervisor_summary=(
                    "The context is useful for strategy but the candidate requested no project section."
                ),
            )

    llm = ZeroBudgetLlm()
    service = ProjectLabService(
        llm=llm,
        facts=snapshot.facts,
        skill_policy=snapshot.skill_policy,
        project_bank=snapshot.project_bank,
        cv_reference=snapshot.cv_template,
    )
    profile = OfferAnalysis(
        company="Medica Europe",
        role="Regulatory Affairs Specialist",
        role_family="Regulatory Affairs",
        language="en",
        summary="Prepare compliant medical-device submissions.",
        responsibilities=["Maintain technical documentation"],
        required_skills=["EU MDR", "ISO 13485"],
        normalized_role="Regulatory Affairs Specialist",
    )

    result = service.suggest(
        ApplicationRow(
            excel_row=2,
            company="Medica Europe",
            role="Regulatory Affairs Specialist",
            url="https://example.test/regulatory-role",
        ),
        "Prepare EU MDR submissions and maintain technical documentation.",
        families=[ProjectLabFamily.SYNTHETIC_PROJECT],
        profile=profile,
        cv_slot_budget=0,
    )

    assert result.report.need_frame.cv_slot_budget == 0
    assert result.report.visible_cv_project_ids == []
    assert "cv_slot_budget vaut 0" in llm.prompt
    assert "execution_approach_rationale" in llm.prompt

    inferred_profile = SimpleNamespace(
        company="Medica Europe",
        role="Regulatory Affairs Specialist",
        role_family="Regulatory Affairs",
        language="en",
        summary="Prepare compliant medical-device submissions.",
        responsibilities=["Maintain technical documentation"],
        required_skills=["EU MDR", "ISO 13485"],
        preferred_skills=[],
        targeted_keywords=[],
        normalized_role="Regulatory Affairs Specialist",
        specialisations=[],
        project_plan=ProjectPlan(
            decision="none",
            rationale="This candidate does not use a visible personal-project section.",
            slots=[],
        ),
        model_dump_json=lambda indent: '{"project_plan":{"decision":"none","slots":[]}}',
    )
    inferred_result = service.suggest(
        ApplicationRow(
            excel_row=3,
            company="Medica Europe",
            role="Regulatory Affairs Specialist",
            url="https://example.test/regulatory-role-2",
        ),
        "Prepare EU MDR submissions and maintain technical documentation.",
        families=[ProjectLabFamily.SYNTHETIC_PROJECT],
        profile=inferred_profile,
    )

    assert inferred_result.report.need_frame.cv_slot_budget == 0


def test_import_without_projects_keeps_project_section_disabled() -> None:
    from test_profile_extraction import _extraction

    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    mapping = analyze_latex_cv(fixture.read_bytes(), filename=fixture.name)
    extraction = _extraction().model_copy(update={"projects": []})

    draft = CandidateDraft.from_extraction(
        import_id="a" * 32,
        mapping=mapping,
        extraction=extraction,
    )

    assert draft.projects == []
    assert draft.project_lab.minimum_visible_projects == 0
    assert draft.project_lab.maximum_visible_projects == 0


def test_workflow_skips_project_lab_when_candidate_disables_projects(tmp_path: Path) -> None:
    class ProjectLabMustNotRun:
        def suggest(self, *_args, **_kwargs):
            raise AssertionError("Project Lab must not run for a zero-project profile")

    workflow = object.__new__(CandidateWorkflowPipeline)
    workflow._snapshot = SimpleNamespace(
        profile=SimpleNamespace(
            project_lab=ProjectLabPolicy(minimum_visible_projects=0, maximum_visible_projects=0)
        )
    )
    workflow._project_lab = ProjectLabMustNotRun()
    workflow._run_dir = tmp_path

    assert workflow._prepare_project_lab(SimpleNamespace(), "offer", SimpleNamespace()) == ""


@pytest.mark.parametrize(
    ("profile_name", "own_name", "forbidden_terms"),
    [
        ("regulatory", "Sofia Martin", ["Projects", "Python", "Data Engineer"]),
        ("frontend", "Noah Williams", ["Python", "Data Engineer", "Machine Learning"]),
    ],
)
def test_complete_cross_domain_profiles_load_and_render_one_page(
    tmp_path: Path,
    profile_name: str,
    own_name: str,
    forbidden_terms: list[str],
) -> None:
    snapshot = CandidateProfileRepository(PROFILE_FIXTURES).load_snapshot(
        PROFILE_FIXTURES / profile_name / "profile.yaml"
    )
    rendered = DocumentRenderer().render_cv(
        snapshot,
        apply_cv_patch(snapshot, CvAdaptationPatch()),
        tmp_path / profile_name,
    )

    assert rendered.page_count == 1
    assert own_name in rendered.extracted_text
    assert all(term not in rendered.extracted_text for term in forbidden_terms)
    if profile_name == "regulatory":
        assert rendered.layout_metrics["font_size_pt"] == 12.0
        assert rendered.layout_metrics["line_height_ratio"] == 1.5
        assert rendered.layout_metrics["layout_trials"] >= 1
        assert rendered.layout_metrics["vertical_coverage_ratio"] >= 0.75
        assert rendered.layout_metrics["at_layout_ceiling"] is True
        assert rendered.layout_metrics["underfilled_at_layout_ceiling"] is False
