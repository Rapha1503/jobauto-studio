from pathlib import Path

import pytest
from test_profile_extraction import _extraction

from jobauto.candidate_draft import (
    CandidateDraft,
    CandidateDraftStore,
    CandidateDraftUpdate,
    DraftAdditionalSection,
    DraftIdentity,
    SkillEvidence,
    SkillUsage,
    update_candidate_draft,
    validate_candidate_draft,
)
from jobauto.latex_cv_source import analyze_latex_cv


def _draft() -> CandidateDraft:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    mapping = analyze_latex_cv(source, filename=fixture.name)
    draft = CandidateDraft.from_extraction(
        import_id="a" * 32,
        mapping=mapping,
        extraction=_extraction(),
    )
    return draft.model_copy(update={"letter_reference": "Dear hiring team."})


def test_candidate_draft_translates_extraction_into_user_policies() -> None:
    draft = _draft()

    assert draft.identity.first_name == "Camille"
    assert draft.locale == "fr-FR"
    assert draft.experiences[0].protected_fields == [
        "organization",
        "role",
        "dates",
    ]
    assert draft.skills[0].usage is SkillUsage.DEFAULT
    assert draft.skills[0].evidence is SkillEvidence.VERIFIED
    assert draft.projects[0].use_mode.value == "reframe"
    assert draft.project_lab.allow_new_project is False
    assert draft.search_preferences.roles.preferred == []


def test_candidate_draft_store_is_versioned_and_atomic(tmp_path: Path) -> None:
    store = CandidateDraftStore(tmp_path / "drafts")
    created = store.create(_draft())
    changed = created.model_copy(update={"warnings": ["Confirm one transferable technology."]})

    saved = store.save(changed, expected_version=1)

    assert saved.version == 2
    assert store.get(saved.draft_id).warnings == ["Confirm one transferable technology."]
    assert not list((tmp_path / "drafts").glob("*.tmp"))


def test_candidate_draft_store_rejects_stale_edit(tmp_path: Path) -> None:
    store = CandidateDraftStore(tmp_path / "drafts")
    created = store.create(_draft())
    store.save(created, expected_version=1)

    with pytest.raises(ValueError, match="version conflict"):
        store.save(created, expected_version=1)


def test_forbidden_skill_cannot_be_required() -> None:
    draft = _draft()
    skill = draft.skills[0]

    with pytest.raises(ValueError, match="forbidden skill"):
        type(skill)(
            **skill.model_dump(exclude={"usage", "evidence"}),
            usage="required",
            evidence="forbidden",
        )


def test_reviewed_update_can_add_candidate_supplied_content() -> None:
    draft = _draft()
    manual_skill = draft.skills[0].model_copy(
        update={"name": "dbt", "source_block_ids": [], "verification_warning": True}
    )
    payload = CandidateDraftUpdate(
        expected_version=draft.version,
        locale=draft.locale,
        identity=draft.identity,
        summary=draft.summary,
        summary_source_block_ids=draft.summary_source_block_ids,
        letter_reference=draft.letter_reference,
        experiences=draft.experiences,
        skills=[*draft.skills, manual_skill],
        projects=draft.projects,
        education=draft.education,
        languages=draft.languages,
        interests=draft.interests,
        project_lab=draft.project_lab,
        search_preferences=draft.search_preferences,
    )

    updated = update_candidate_draft(draft, payload)

    assert updated.status.value == "needs_review"
    assert updated.skills[-1].name == "dbt"
    assert updated.skills[-1].source_block_ids == []
    assert updated.skills[-1].verification_warning is True


def test_review_update_preserves_extracted_additional_sections() -> None:
    draft = _draft().model_copy(
        update={
            "additional_sections": [
                DraftAdditionalSection(
                    label="Publications",
                    content="Two peer-reviewed articles",
                    source_block_ids=["other"],
                )
            ]
        }
    )
    payload = CandidateDraftUpdate(
        expected_version=draft.version,
        locale=draft.locale,
        identity=draft.identity,
        summary=draft.summary,
        summary_source_block_ids=draft.summary_source_block_ids,
        letter_reference=draft.letter_reference,
        experiences=draft.experiences,
        skills=draft.skills,
        projects=draft.projects,
        education=draft.education,
        languages=draft.languages,
        interests=draft.interests,
        project_lab=draft.project_lab,
        search_preferences=draft.search_preferences,
    )

    updated = update_candidate_draft(draft, payload)

    assert updated.additional_sections == draft.additional_sections


def test_validation_accepts_reviewed_manual_items_but_rejects_foreign_provenance() -> None:
    draft = _draft()
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    mapping = analyze_latex_cv(fixture.read_bytes(), filename=fixture.name)

    assert validate_candidate_draft(draft, mapping).valid is True

    foreign = draft.model_copy(
        update={
            "identity": DraftIdentity(
                **draft.identity.model_dump(exclude={"source_block_ids"}),
                source_block_ids=["foreign"],
            )
        }
    )
    result = validate_candidate_draft(foreign, mapping)

    assert result.valid is False
    assert any("unknown CV blocks" in error for error in result.errors)


def test_validation_requires_candidate_identity() -> None:
    draft = _draft().model_copy(
        update={"identity": _draft().identity.model_copy(update={"email": None})}
    )
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    mapping = analyze_latex_cv(fixture.read_bytes(), filename=fixture.name)

    result = validate_candidate_draft(draft, mapping)

    assert result.valid is False
    assert "Candidate email is required." in result.errors


def test_reference_letter_is_optional() -> None:
    draft = _draft().model_copy(update={"letter_reference": None})
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    mapping = analyze_latex_cv(fixture.read_bytes(), filename=fixture.name)

    assert validate_candidate_draft(draft, mapping).valid is True


def test_validation_accepts_noncanonical_evidence_sections() -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    mapping = analyze_latex_cv(fixture.read_bytes(), filename=fixture.name)
    draft = _draft().model_copy(
        update={
            "experiences": [],
            "projects": [],
            "education": [],
            "additional_sections": [
                DraftAdditionalSection(
                    label="Publications",
                    content="Two peer-reviewed articles",
                    source_block_ids=[mapping.blocks[-1].block_id],
                )
            ],
        }
    )

    assert validate_candidate_draft(draft, mapping).valid is True
