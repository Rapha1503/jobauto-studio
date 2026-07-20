from __future__ import annotations

from pathlib import Path

import pytest

from jobauto.candidate_context import CandidateContext
from jobauto.candidate_pipeline import CandidatePipeline
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.codex_client import GenerationPhase
from jobauto.document_patch import CvAdaptationPatch, CvFieldChange
from jobauto.document_renderer import DocumentRenderer
from jobauto.models import (
    AdaptationDecision,
    ApplicationBrief,
    ApplicationRow,
    BaselineCvAssessment,
    CandidateLetterDraft,
    EvidenceMapping,
    OfferRequirement,
    ProjectPlan,
    ProjectSlotPlan,
    RenderedRequirementCoverage,
    RoleFamily,
    SkillPlan,
    SkillPlanItem,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config" / "profiles"
OFFERS = Path(__file__).parent / "fixtures" / "offers"


def _snapshot(candidate: str):
    return CandidateProfileRepository(PROFILES).load_snapshot(PROFILES / candidate / "profile.yaml")


def _success_brief(candidate: str) -> ApplicationBrief:
    is_alex = candidate == "example"
    project_id = "energy_forecasting" if is_alex else "medical_image_triage"
    role = "Data Engineer" if is_alex else "Machine Learning Engineer"
    family = RoleFamily.DATA_ENGINEER if is_alex else RoleFamily.MACHINE_LEARNING_ENGINEER
    requirement = OfferRequirement(
        requirement_id="req.core",
        requirement="Build reliable Python data products",
        source_excerpt="Build reliable Python data products",
        priority="must",
        kind="technical_skill",
    )
    categories = ["Core Engineering", "Applied Methods", "Delivery"]
    return ApplicationBrief(
        company="ExampleCo",
        role=role,
        role_family=family,
        language="en" if is_alex else "fr",
        summary="A synthetic role used to verify candidate isolation.",
        responsibilities=["Build reliable data products"],
        required_skills=["Python"],
        preferred_skills=[],
        company_details=[],
        seniority="junior",
        normalized_role=role,
        targeted_keywords=["Python", "data quality"],
        cv_angle="Use the candidate's own verified technical evidence.",
        letter_angle="Connect verified evidence to the operational mission.",
        adaptation_guidance=[],
        open_role=role,
        sector="Energy" if is_alex else "Digital health",
        specialisations=["Reliable data products"],
        requirements=[requirement],
        evidence_mappings=[
            EvidenceMapping(
                requirement_id="req.core",
                evidence_level="verified",
                fact_ids=["identity.current"],
                rationale="The selected candidate has a verified adjacent technical foundation.",
            )
        ],
        adaptation_decisions=[
            AdaptationDecision(
                surface="both",
                decision="Use the candidate's verified core experience.",
                rationale="It provides the strongest concise argument for the synthetic offer.",
                fact_ids=["identity.current"],
            )
        ],
        project_plan=ProjectPlan(
            decision="create",
            rationale="Keep the verified project and reserve one complementary project slot.",
            central_gaps=["Complementary operational context"],
            slots=[
                ProjectSlotPlan(
                    slot=1,
                    mode="reuse",
                    source_project_id=project_id,
                    requirement_ids=["req.core"],
                    rationale="The verified project supports the central technical requirement.",
                ),
                ProjectSlotPlan(
                    slot=2,
                    mode="create",
                    requirement_ids=["req.core"],
                    rationale="A complementary slot can cover the remaining operational angle.",
                ),
            ],
        ),
        skill_plan=SkillPlan(
            categories=categories,
            items=[
                SkillPlanItem(
                    name="Python",
                    category=categories[0],
                    kind="language",
                    priority="must",
                    evidence_level="verified",
                    requirement_ids=["req.core"],
                ),
                SkillPlanItem(
                    name="Model evaluation" if not is_alex else "Data quality",
                    category=categories[1],
                    kind="technical_capability",
                    priority="important",
                    evidence_level="verified",
                ),
                SkillPlanItem(
                    name="MLflow" if not is_alex else "BigQuery",
                    category=categories[2],
                    kind="platform",
                    priority="baseline",
                    evidence_level="verified",
                ),
            ],
            rationale="Use three broad capability families without copying business nouns.",
        ),
        baseline_cv_assessment=BaselineCvAssessment(
            decision="adapt",
            ats_score=72,
            confidence="high",
            role_positioning_matches=True,
            language_matches=True,
            material_gaps=["The source CV needs a clearer role-specific angle."],
            improvable_requirement_ids=["req.core"],
            requirement_coverage=[
                RenderedRequirementCoverage(
                    requirement_id="req.core",
                    coverage="indirect",
                    placements=["Experience"],
                    rationale="The underlying evidence exists but is not positioned clearly.",
                )
            ],
            rationale="A focused adaptation can improve visible coverage of the central requirement.",
        ),
    )


def _terminal_gap_brief() -> ApplicationBrief:
    brief = _success_brief("example")
    requirement = OfferRequirement(
        requirement_id="req.medical_experience",
        requirement="Professional medical-image classification experience",
        source_excerpt="expérience professionnelle obligatoire en classification d'images médicales",
        priority="must",
        kind="experience",
    )
    return brief.model_copy(
        update={
            "requirements": [requirement],
            "evidence_mappings": [
                EvidenceMapping(
                    requirement_id=requirement.requirement_id,
                    evidence_level="unsupported",
                    fact_ids=[],
                    rationale="The selected candidate has no verified professional medical-imaging experience.",
                )
            ],
        }
    )


class MatrixWriterLlm:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.calls: list[tuple[type, GenerationPhase]] = []

    def complete_json(self, prompt, response_model, phase, **_kwargs):
        self.prompts.append(prompt)
        self.calls.append((response_model, phase))
        is_alex = '"candidate_id":"alex-morgan"' in prompt
        if response_model is CvAdaptationPatch:
            summary = (
                "Data engineer focused on reliable analytics pipelines, data quality and useful "
                "data products, with verified experience translating operational requirements "
                "into maintainable Python and SQL workflows for business teams."
                if is_alex
                else "Ingénieur machine learning spécialisé dans l'évaluation de modèles, "
                "l'analyse d'erreurs et les applications de vision, avec une expérience vérifiée "
                "de workflows Python et PyTorch reproductibles pour des équipes produit."
            )
            return CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="summary.text",
                        value=summary,
                        fact_ids=["identity.current"],
                    )
                ]
            )
        if response_model is CandidateLetterDraft:
            name = "Alex Morgan" if is_alex else "Jamie Chen"
            return CandidateLetterDraft(
                greeting="Dear hiring team," if is_alex else "Madame, Monsieur,",
                paragraphs=[
                    "The candidate's verified technical experience supports a concise and relevant application for this role."
                    if is_alex
                    else "L'expérience technique vérifiée du candidat soutient une candidature concise et pertinente pour ce poste."
                ],
                closing=f"Kind regards,\n{name}" if is_alex else f"Cordialement,\n{name}",
                used_fact_ids=["identity.current"],
            )
        raise AssertionError(f"Unexpected model: {response_model}")


@pytest.mark.parametrize(
    ("candidate", "brief", "offer_name", "own_name", "foreign_name"),
    [
        ("example", _success_brief("example"), "shared_en.txt", "Alex Morgan", "Jamie Chen"),
        ("example-b", _success_brief("example-b"), "shared_en.txt", "Jamie Chen", "Alex Morgan"),
        (
            "example-b",
            _success_brief("example-b"),
            "candidate_b_only_fr.txt",
            "Jamie Chen",
            "Alex Morgan",
        ),
    ],
)
def test_two_candidate_matrix_isolates_prompts_and_rendered_files(
    tmp_path: Path,
    candidate: str,
    brief: ApplicationBrief,
    offer_name: str,
    own_name: str,
    foreign_name: str,
) -> None:
    snapshot = _snapshot(candidate)
    context = CandidateContext.from_snapshot(snapshot)
    llm = MatrixWriterLlm()
    pipeline = CandidatePipeline.for_candidate(llm, snapshot, context)
    offer = (OFFERS / offer_name).read_text(encoding="utf-8")

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="ExampleCo",
            role=brief.role,
            url="https://example.test/job",
        ),
        offer,
        brief=brief,
    )
    renderer = DocumentRenderer()
    cv = renderer.render_cv(snapshot, package.cv, tmp_path / candidate / offer_name / "cv")
    letter = renderer.render_letter(
        snapshot,
        package.letter,
        tmp_path / candidate / offer_name / "letter",
    )

    combined_prompts = "\n".join(llm.prompts)
    assert own_name in context.serialized
    assert foreign_name not in context.serialized
    assert own_name in combined_prompts
    assert foreign_name not in combined_prompts
    assert own_name in cv.extracted_text
    assert own_name in letter.extracted_text
    assert foreign_name not in cv.extracted_text
    assert foreign_name not in letter.extracted_text


def test_cross_candidate_terminal_experience_gap_blocks_before_writers() -> None:
    snapshot = _snapshot("example")
    context = CandidateContext.from_snapshot(snapshot)
    llm = MatrixWriterLlm()
    pipeline = CandidatePipeline.for_candidate(llm, snapshot, context)

    with pytest.raises(ValueError, match="terminal candidate fit gap"):
        pipeline.generate_candidate_documents(
            ApplicationRow(
                excel_row=1,
                company="ExampleCo",
                role="Medical Imaging Engineer",
                url="https://example.test/medical-role",
            ),
            (OFFERS / "candidate_b_only_fr.txt").read_text(encoding="utf-8"),
            brief=_terminal_gap_brief(),
        )

    assert llm.calls == []
