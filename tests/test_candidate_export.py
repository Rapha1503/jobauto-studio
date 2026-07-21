from pathlib import Path

import pytest
from test_profile_extraction import _extraction

from jobauto.adaptation_policy import (
    STUDIO_ADAPTATION_PRESETS,
    FidelityLevel,
    SectionPolicy,
)
from jobauto.candidate_context import CandidateContext, ContextPurpose
from jobauto.candidate_draft import CandidateDraft, DraftStatus, SkillEvidence
from jobauto.candidate_export import _adaptation_policy_payload, export_candidate_draft
from jobauto.candidate_profile import CvBackend
from jobauto.document_patch import (
    CvAdaptationPatch,
    CvFieldChange,
    CvProjectSectionChange,
    CvSkillSectionChange,
    apply_cv_patch,
    editable_cv_source_index,
)
from jobauto.document_renderer import DocumentRenderer
from jobauto.latex_cv_source import TexBlockKind, analyze_latex_cv, apply_block_replacements
from jobauto.models import CandidateLetterDraft, validate_candidate_letter_claim_values
from jobauto.profile_extraction import CandidateProfileExtraction


def _validated_draft(source: bytes, filename: str) -> tuple[CandidateDraft, object]:
    mapping = analyze_latex_cv(source, filename=filename)
    draft = CandidateDraft.from_extraction(
        import_id="b" * 32,
        mapping=mapping,
        extraction=_extraction(),
    )
    draft = draft.model_copy(
        update={
            "status": DraftStatus.VALIDATED,
            "letter_reference": "Madame, Monsieur, je vous adresse ma candidature.",
            "search_preferences": draft.search_preferences.model_copy(
                update={
                    "roles": draft.search_preferences.roles.model_copy(
                        update={"preferred": ["Data Engineer", "AI Engineer"]}
                    )
                }
            ),
            "skills": [
                *draft.skills,
                draft.skills[0].model_copy(
                    update={
                        "name": "dbt",
                        "evidence": SkillEvidence.TRANSFERABLE,
                        "verification_warning": True,
                        "source_block_ids": [],
                    }
                ),
            ],
        }
    )
    return draft, mapping


def test_validated_draft_exports_one_source_preserving_snapshot(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)

    profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    assert snapshot.profile.cv_backend is CvBackend.SOURCE_PRESERVING
    assert snapshot.cv_mapping is not None
    assert snapshot.cv_mapping.mapping_hash == mapping.mapping_hash
    assert snapshot.profile.cv_model_path.read_bytes() == source
    assert snapshot.cv_template_bytes == source
    assert snapshot.profile.identity.first_name == "Camille"
    assert snapshot.cv_source.summary == "Profil données et systèmes énergétiques."
    assert snapshot.project_bank.entries[0].use_mode == "reframe"
    assert snapshot.search_preferences.roles.preferred == ["Data Engineer", "AI Engineer"]
    assert snapshot.submission_preferences.max_applications_per_campaign == 5
    assert snapshot.profile.submission_preferences_path is not None
    assert snapshot.profile.submission_preferences_path.is_file()
    assert snapshot.profile.form_profile_path is not None
    assert snapshot.profile.form_profile_path.is_file()
    form_profile = snapshot.profile.form_profile_path.read_text(encoding="utf-8")
    assert draft.experiences[0].organization in form_profile
    assert draft.experiences[0].role in form_profile
    assert '"location": "None"' not in form_profile
    assert snapshot.profile.project_lab.allow_new_project is False
    assert snapshot.profile.project_lab.minimum_visible_projects == 1
    expected_metric_ids = [
        f"experience.{experience.experience_id}.metric.{index + 1}"
        for experience in draft.experiences
        for index, _metric in enumerate(experience.metrics)
    ]
    assert snapshot.profile.protected_claims == expected_metric_ids
    assert (
        snapshot.adaptation_policy.documents["cv"].sections["experience"].protected_fact_ids == []
    )
    assert snapshot.skill_policy.context_data()["warnings"] == ["dbt"]
    assert "dbt" in str(snapshot.skill_policy.context_data()["transferable"])
    assert profile_path.is_file()

    context = CandidateContext.from_snapshot(snapshot)
    assert context.payload["candidate_id"] == snapshot.profile.candidate_id
    assert "Camille" in context.serialized
    assert "PrivateCandidate" not in context.serialized
    assert "PrivateEmployer" not in context.serialized
    assert context.payload["project_lab"]["allow_new_project"] is False
    education_block = next(
        block
        for block in context.payload["source_preserving_blocks"]
        if block["kind"] == "education"
    )
    assert "2021--2026" in education_block["latex"]
    assert education_block["fidelity"] == "locked"


def test_project_can_be_hidden_from_baseline_but_selected_for_tailored_cv(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)
    bonus_project = draft.projects[0].model_copy(
        update={"project_id": "bonus", "visible_by_default": False, "cv_eligible": True}
    )
    draft = draft.model_copy(update={"projects": [bonus_project]})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    assert snapshot.project_bank.entries[0].visibility == "cv_project"
    assert snapshot.cv_source.projects == []


def test_reexport_refreshes_existing_candidate_profile_files(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)
    profiles_root = tmp_path / "profiles"
    profile_path, first_snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=profiles_root,
    )
    updated_project = draft.projects[0].model_copy(
        update={"visible_by_default": False, "cv_eligible": True}
    )
    updated_draft = draft.model_copy(update={"projects": [updated_project]})

    updated_path, updated_snapshot = export_candidate_draft(
        draft=updated_draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=profiles_root,
    )

    assert updated_path == profile_path
    assert updated_snapshot.snapshot_hash != first_snapshot.snapshot_hash
    assert updated_snapshot.project_bank.entries[0].visibility == "cv_project"
    assert updated_snapshot.cv_source.projects == []


def test_export_protects_metrics_only_when_the_candidate_requests_it(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)
    experience = draft.experiences[0].model_copy(
        update={
            "metrics": ["Budgets up to EUR 180,000"],
            "protected_fields": ["organization", "role", "dates", "metrics"],
        }
    )
    draft = draft.model_copy(update={"experiences": [experience, *draft.experiences[1:]]})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    expected = [
        f"experience.{experience.experience_id}.metric.{index + 1}"
        for index, _metric in enumerate(experience.metrics)
    ]
    assert snapshot.profile.protected_claims == expected
    assert (
        snapshot.adaptation_policy.documents["cv"].sections["experience"].protected_fact_ids
        == expected
    )


def test_optional_metric_value_remains_immutable_when_used(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)
    experience = draft.experiences[0].model_copy(update={"metrics": ["Budgets up to EUR 180,000"]})
    draft = draft.model_copy(update={"experiences": [experience, *draft.experiences[1:]]})
    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    metric_id = snapshot.profile.protected_claims[0]
    assert (
        snapshot.adaptation_policy.documents["cv"].sections["experience"].protected_fact_ids == []
    )

    snapshot.require_protected_claim_values(
        "Managed supplier budgets up to 180 000 €.",
        [metric_id],
    )
    snapshot.require_protected_claim_values("Managed supplier budgets.", [])

    with pytest.raises(ValueError, match="Protected claim values changed"):
        snapshot.require_protected_claim_values(
            "Managed supplier budgets up to 190 000 €.",
            [metric_id],
        )


def _export_exhibition_profile_with_metric(tmp_path: Path, *, protect_metric: bool = False):
    fixture = Path(__file__).parent / "fixtures" / "cv" / "exhibition_producer_en.tex"
    source = fixture.read_bytes()
    mapping = analyze_latex_cv(source, filename=fixture.name)
    experience_block = next(
        block for block in mapping.blocks if block.kind is TexBlockKind.EXPERIENCE
    )
    extraction = CandidateProfileExtraction.model_validate(
        {
            "locale": "en-GB",
            "identity": {
                "first_name": "Maya",
                "last_name": "Laurent",
                "email": "maya.laurent@example.test",
                "location": "Paris",
                "headline": "Exhibition Producer",
                "source_block_ids": [
                    next(
                        block.block_id
                        for block in mapping.blocks
                        if block.kind is TexBlockKind.IDENTITY
                    )
                ],
            },
            "experiences": [
                {
                    "experience_id": "atelier",
                    "organization": "Atelier Horizon",
                    "role": "Exhibition Producer",
                    "dates": "2021--2025",
                    "facts": [
                        "Produced six temporary exhibitions from initial brief to opening.",
                        "Managed production schedules and budgets up to EUR 180,000.",
                    ],
                    "metrics": ["Budgets up to EUR 180,000"],
                    "source_block_ids": [experience_block.block_id],
                }
            ],
        }
    )
    draft = CandidateDraft.from_extraction(
        import_id="e" * 32,
        mapping=mapping,
        extraction=extraction,
    )
    if protect_metric:
        experience = draft.experiences[0].model_copy(update={"protected_fields": ["metrics"]})
        draft = draft.model_copy(update={"experiences": [experience]})
    draft = draft.model_copy(update={"status": DraftStatus.VALIDATED})
    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    return snapshot, experience_block


def test_protected_metric_may_remain_unchanged_when_another_experience_bullet_changes(
    tmp_path: Path,
) -> None:
    snapshot, _experience_block = _export_exhibition_profile_with_metric(
        tmp_path,
        protect_metric=True,
    )

    document = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="experience.0.bullet.0",
                    value=(
                        "Produced six temporary exhibitions from brief to opening, "
                        "coordinating artists, curators, venues and accessibility partners."
                    ),
                    fact_ids=["experience.atelier.fact.1"],
                )
            ]
        ),
    )

    assert "EUR 180,000" in document.document.experience[0].bullets[1]

    with pytest.raises(ValueError, match="Protected claim values changed or were omitted"):
        apply_cv_patch(
            snapshot,
            CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="experience.0.bullet.1",
                        value="Managed production schedules and supplier consultations.",
                        fact_ids=["experience.atelier.fact.1"],
                    )
                ]
            ),
        )


def test_source_block_provenance_cannot_change_or_contradict_a_metric(tmp_path: Path) -> None:
    snapshot, experience_block = _export_exhibition_profile_with_metric(tmp_path)
    source_id = f"source_block.{experience_block.block_id}"

    snapshot.require_supported_claim_values("Budget managed: €180k.", [source_id])
    with pytest.raises(ValueError, match="Unsupported quantitative claim values"):
        snapshot.require_supported_claim_values("Budget managed: EUR 190,000.", [source_id])
    with pytest.raises(ValueError, match="Unsupported quantitative claim values"):
        snapshot.require_supported_claim_values(
            "Budget increased from EUR 180,000 to EUR 190,000.",
            [source_id],
        )


def test_calendar_year_is_not_treated_as_an_invented_metric(tmp_path: Path) -> None:
    snapshot, _experience_block = _export_exhibition_profile_with_metric(tmp_path)

    assert (
        snapshot.facts.require("experience.atelier.dates").claim
        == "Atelier Horizon - Exhibition Producer: 2021--2025"
    )
    snapshot.require_supported_claim_values(
        "Exhibition producer since September 2021.",
        ["experience.atelier.dates"],
    )
    with pytest.raises(ValueError, match="Unsupported quantitative claim values"):
        snapshot.require_supported_claim_values(
            "Coordinated 2021 partner organisations.",
            ["experience.atelier.fact.1"],
        )


def test_cv_path_rejects_a_changed_metric_hidden_behind_source_provenance(
    tmp_path: Path,
) -> None:
    snapshot, experience_block = _export_exhibition_profile_with_metric(tmp_path)

    with pytest.raises(ValueError, match="Unsupported quantitative claim values"):
        apply_cv_patch(
            snapshot,
            CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="experience.0.bullet.0",
                        value=(
                            "Managed production schedules, supplier consultations and budgets "
                            "up to EUR 190,000."
                        ),
                        fact_ids=[f"source_block.{experience_block.block_id}"],
                    )
                ]
            ),
        )


def test_letter_path_rejects_a_changed_metric_hidden_behind_source_provenance(
    tmp_path: Path,
) -> None:
    snapshot, experience_block = _export_exhibition_profile_with_metric(tmp_path)
    letter = CandidateLetterDraft(
        greeting="Dear Hiring Team,",
        paragraphs=["I managed supplier budgets up to EUR 190,000."],
        closing="Yours sincerely, Maya Laurent",
        used_fact_ids=[f"source_block.{experience_block.block_id}"],
    )

    with pytest.raises(ValueError, match="Unsupported quantitative claim values"):
        validate_candidate_letter_claim_values(snapshot, letter, "No quantitative offer claims.")


def test_letter_claim_values_may_come_from_the_offer(tmp_path: Path) -> None:
    snapshot, _experience_block = _export_exhibition_profile_with_metric(tmp_path)
    letter = CandidateLetterDraft(
        greeting="Dear Hiring Team,",
        paragraphs=["Your network of 250 partner organisations particularly interests me."],
        closing="Yours sincerely, Maya Laurent",
        used_fact_ids=["profile.summary"],
    ).validate_for_snapshot(snapshot)

    validate_candidate_letter_claim_values(
        snapshot,
        letter,
        "The role works with a network of 250 partner organisations.",
    )


def test_adaptation_policy_export_does_not_assume_an_identity_mapping() -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    draft, mapping = _validated_draft(fixture.read_bytes(), fixture.name)
    mapping = mapping.model_copy(
        update={
            "blocks": [block for block in mapping.blocks if block.kind is not TexBlockKind.IDENTITY]
        }
    )

    payload = _adaptation_policy_payload(draft, mapping)

    assert "identity" not in payload["documents"]["cv"]["sections"]


def test_export_does_not_reintroduce_a_missing_project_section(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    original_mapping = analyze_latex_cv(source, filename=fixture.name)
    projects = next(
        block for block in original_mapping.blocks if block.kind is TexBlockKind.PROJECTS
    )
    source = apply_block_replacements(source, original_mapping, {projects.block_id: ""})
    mapping = analyze_latex_cv(source, filename=fixture.name)
    extraction = _extraction().model_copy(update={"projects": []})
    draft = CandidateDraft.from_extraction(
        import_id="c" * 32,
        mapping=mapping,
        extraction=extraction,
    ).model_copy(update={"status": DraftStatus.VALIDATED})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    assert snapshot.profile.project_lab.maximum_visible_projects == 0
    assert "projects" not in snapshot.adaptation_policy.documents["cv"].section_order
    assert "projects" not in snapshot.adaptation_policy.documents["cv"].sections


def test_export_accepts_a_source_cv_without_a_skills_section(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    original_mapping = analyze_latex_cv(source, filename=fixture.name)
    skills = next(block for block in original_mapping.blocks if block.kind is TexBlockKind.SKILLS)
    source = apply_block_replacements(source, original_mapping, {skills.block_id: ""})
    mapping = analyze_latex_cv(source, filename=fixture.name)
    extraction = _extraction().model_copy(update={"skills": []})
    draft = CandidateDraft.from_extraction(
        import_id="f" * 32,
        mapping=mapping,
        extraction=extraction,
    ).model_copy(update={"status": DraftStatus.VALIDATED})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    rendered = DocumentRenderer().render_cv(
        snapshot,
        apply_cv_patch(snapshot, CvAdaptationPatch()),
        tmp_path / "rendered-no-skills",
    )

    assert snapshot.skill_policy.verified_groups == set()
    assert "skills" not in snapshot.adaptation_policy.documents["cv"].sections
    assert rendered.page_count == 1
    assert "Compétences" not in rendered.extracted_text
    assert rendered.layout_metrics["font_size_pt"] == 12.0
    assert rendered.layout_metrics["line_height_ratio"] == 1.5
    assert rendered.layout_metrics["section_spacing_pt"] > 0
    assert rendered.layout_metrics["vertical_coverage_ratio"] > 0.62


def test_export_promotes_unambiguous_custom_project_and_skill_sections(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "exhibition_producer_en.tex"
    source = fixture.read_bytes()
    mapping = analyze_latex_cv(source, filename=fixture.name)
    productions = next(block for block in mapping.blocks if block.label == "Selected Productions")
    capabilities = next(
        block for block in mapping.blocks if block.label == "Professional Capabilities"
    )
    extraction = CandidateProfileExtraction.model_validate(
        {
            "identity": {
                "first_name": "Maya",
                "last_name": "Laurent",
                "email": "maya.laurent@example.test",
                "phone": "+33 1 00 00 00 02",
                "location": "Paris",
                "headline": "Exhibition Producer | Cultural Programmes | Paris",
                "source_block_ids": ["identity"],
            },
            "projects": [
                {
                    "project_id": "city_in_motion",
                    "title": "City in Motion - Touring Installation",
                    "description": ["Coordinated three venue adaptations and installation crews."],
                    "source_block_ids": [productions.block_id],
                }
            ],
            "skills": [
                {
                    "name": "Production planning",
                    "category": "Production",
                    "source_block_ids": [capabilities.block_id],
                }
            ],
        }
    )
    draft = CandidateDraft.from_extraction(
        import_id="a" * 32,
        mapping=mapping,
        extraction=extraction,
    )
    draft = draft.model_copy(
        update={
            "status": DraftStatus.VALIDATED,
            "projects": [draft.projects[0].model_copy(update={"project_id": "city-in-motion"})],
        }
    )

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    promoted = {block.label: block for block in snapshot.cv_mapping.blocks}
    assert promoted["Selected Productions"].kind is TexBlockKind.PROJECTS
    assert promoted["Professional Capabilities"].kind is TexBlockKind.SKILLS
    assert (
        promoted["Selected Productions"].policy.fidelity
        is STUDIO_ADAPTATION_PRESETS["balanced"]["projects"]
    )
    assert (
        promoted["Professional Capabilities"].policy.fidelity
        is STUDIO_ADAPTATION_PRESETS["balanced"]["skills"]
    )
    assert snapshot.project_bank.entries[0].id == "city_in_motion"
    assert "projects" in snapshot.adaptation_policy.documents["cv"].sections
    assert "skills" in snapshot.adaptation_policy.documents["cv"].sections
    evidence_id = sorted(snapshot.evidence_ids)[0]
    adapted = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            projects=CvProjectSectionChange(
                entries=snapshot.cv_source.projects,
                fact_ids=[evidence_id],
            ),
            skills=CvSkillSectionChange(
                groups=snapshot.cv_source.skills,
                fact_ids=[evidence_id],
            ),
        ),
    )
    assert adapted.document.projects == snapshot.cv_source.projects
    assert adapted.document.skills == snapshot.cv_source.skills


def test_semantic_promotion_preserves_an_explicit_user_fidelity(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "exhibition_producer_en.tex"
    source = fixture.read_bytes()
    mapping = analyze_latex_cv(source, filename=fixture.name)
    production = next(block for block in mapping.blocks if block.label == "Selected Productions")
    mapping = mapping.model_copy(
        update={
            "blocks": [
                block.model_copy(update={"policy": SectionPolicy(fidelity=FidelityLevel.LOCKED)})
                if block.block_id == production.block_id
                else block
                for block in mapping.blocks
            ]
        }
    )
    extraction = CandidateProfileExtraction.model_validate(
        {
            "identity": {
                "first_name": "Maya",
                "last_name": "Laurent",
                "email": "maya.laurent@example.test",
                "source_block_ids": ["identity"],
            },
            "projects": [
                {
                    "project_id": "city_in_motion",
                    "title": "City in Motion",
                    "description": ["Coordinated a touring installation."],
                    "source_block_ids": [production.block_id],
                }
            ],
        }
    )
    draft = CandidateDraft.from_extraction(
        import_id="f" * 32,
        mapping=mapping,
        extraction=extraction,
    ).model_copy(update={"status": DraftStatus.VALIDATED})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    promoted = next(
        block for block in snapshot.cv_mapping.blocks if block.label == "Selected Productions"
    )
    assert promoted.kind is TexBlockKind.PROJECTS
    assert promoted.policy.fidelity is FidelityLevel.LOCKED


def test_exported_cv_ignores_empty_semantic_fields_without_a_tex_section(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    original_mapping = analyze_latex_cv(source, filename=fixture.name)
    interests = next(
        block for block in original_mapping.blocks if block.kind is TexBlockKind.INTERESTS
    )
    source = apply_block_replacements(source, original_mapping, {interests.block_id: ""})
    mapping = analyze_latex_cv(source, filename=fixture.name)
    extraction = _extraction().model_copy(update={"interests": []})
    draft = CandidateDraft.from_extraction(
        import_id="e" * 32,
        mapping=mapping,
        extraction=extraction,
    ).model_copy(update={"status": DraftStatus.VALIDATED})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    assert "interests" not in snapshot.adaptation_policy.documents["cv"].sections
    assert "interests.text" not in editable_cv_source_index(snapshot)


def test_export_exposes_custom_tex_sections_as_verified_source_evidence(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes().replace(
        b"\\end{document}",
        b"\\cvsection{Certifications}\nISO 13485 Lead Auditor\n\\end{document}",
    )
    mapping = analyze_latex_cv(source, filename=fixture.name)
    draft = CandidateDraft.from_extraction(
        import_id="d" * 32,
        mapping=mapping,
        extraction=_extraction(),
    ).model_copy(update={"status": DraftStatus.VALIDATED})

    _profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    context = CandidateContext.from_snapshot(snapshot)

    assert "source_block.other" in snapshot.evidence_ids
    assert "source_block.languages" in snapshot.evidence_ids
    assert "source_block.skills" in snapshot.evidence_ids
    assert "source_block.identity" not in snapshot.evidence_ids
    snapshot.require_evidence_ids(
        ["source_block.other", "source_block.languages", "source_block.skills"]
    )
    certification = next(
        block
        for block in context.payload["additional_evidence_blocks"]
        if block["label"] == "Certifications"
    )
    assert certification["evidence_id"] == "source_block.other"
    assert "ISO 13485 Lead Auditor" in context.prompt_view(ContextPurpose.STRATEGY).serialized
    rendered = DocumentRenderer().render_cv(
        snapshot,
        apply_cv_patch(snapshot, CvAdaptationPatch()),
        tmp_path / "rendered",
    )
    assert "Certifications" in rendered.extracted_text
    assert "ISO 13485 Lead Auditor" in rendered.extracted_text


def test_experience_metrics_remain_evidence_without_becoming_extra_cv_bullets(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)
    experience = draft.experiences[0].model_copy(update={"metrics": ["macro-F1 0.91"]})
    draft = draft.model_copy(update={"experiences": [experience]})

    profile_path, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    assert all("macro-F1" not in bullet for bullet in snapshot.cv_source.experience[0].bullets)
    assert "macro-F1 0.91" in snapshot.facts.prompt_text()
    assert "experience.gridlab.metric.1" in snapshot.evidence_ids
    assert profile_path.is_file()


def test_export_is_idempotent_for_same_validated_version(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)

    first_path, first = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    second_path, second = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )

    assert second_path == first_path
    assert second.snapshot_hash == first.snapshot_hash


def test_two_contradictory_imports_do_not_share_candidate_context(tmp_path: Path) -> None:
    data_fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    engineering_fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_engineering.tex"
    data_source = data_fixture.read_bytes()
    engineering_source = engineering_fixture.read_bytes()
    data_draft, data_mapping = _validated_draft(data_source, data_fixture.name)
    engineering_mapping = analyze_latex_cv(engineering_source, filename=engineering_fixture.name)
    engineering_extraction = CandidateProfileExtraction.model_validate(
        {
            "identity": {
                "first_name": "Noah",
                "last_name": "Bennett",
                "email": "noah.bennett@example.test",
                "phone": "+44 20 0000 0000",
                "location": "Bristol",
                "headline": "Mechanical Systems Engineer | Simulation, CAD | Bristol",
                "source_block_ids": ["identity"],
            },
            "summary": "Mechanical engineer focused on structural simulation and test analysis.",
            "summary_source_block_ids": ["summary"],
            "experiences": [
                {
                    "experience_id": "aeroworks",
                    "organization": "AeroWorks",
                    "role": "Mechanical Engineering Intern",
                    "dates": "2025--2026",
                    "sector": "Aerospace",
                    "tools": ["CAD"],
                    "facts": ["Analysed structural test results."],
                    "source_block_ids": ["experience"],
                }
            ],
            "projects": [
                {
                    "project_id": "wing_load_analysis",
                    "title": "Wing load analysis",
                    "stack": ["MATLAB", "ANSYS"],
                    "description": ["Built a finite-element load-case study."],
                    "source_block_ids": ["projects"],
                }
            ],
            "skills": [
                {"name": "MATLAB", "category": "Simulation", "source_block_ids": ["skills"]},
                {"name": "ANSYS", "category": "Simulation", "source_block_ids": ["skills"]},
                {"name": "SolidWorks", "category": "Design", "source_block_ids": ["skills"]},
            ],
            "education": [
                {
                    "institution": "West Engineering School",
                    "program": "MSc Mechanical Engineering",
                    "dates": "2022--2026",
                    "source_block_ids": ["education"],
                }
            ],
            "languages": ["English native", "French B1"],
            "interests": ["Aviation", "Cycling"],
        }
    )
    engineering_draft = CandidateDraft.from_extraction(
        import_id="c" * 32,
        mapping=engineering_mapping,
        extraction=engineering_extraction,
    ).model_copy(
        update={
            "locale": "en-GB",
            "letter_reference": "Dear hiring team, I am applying for this engineering role.",
            "status": DraftStatus.VALIDATED,
        }
    )

    _, data_snapshot = export_candidate_draft(
        draft=data_draft,
        tex_source=data_source,
        mapping=data_mapping,
        profiles_root=tmp_path / "profiles",
    )
    _, engineering_snapshot = export_candidate_draft(
        draft=engineering_draft,
        tex_source=engineering_source,
        mapping=engineering_mapping,
        profiles_root=tmp_path / "profiles",
    )
    data_context = CandidateContext.from_snapshot(data_snapshot).serialized
    engineering_context = CandidateContext.from_snapshot(engineering_snapshot).serialized

    assert "Camille" in data_context and "Python" in data_context
    assert "Noah" not in data_context and "ANSYS" not in data_context
    assert "Noah" in engineering_context and "ANSYS" in engineering_context
    assert "Camille" not in engineering_context and "Python" not in engineering_context
