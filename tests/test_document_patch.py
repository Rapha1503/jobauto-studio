from __future__ import annotations

from pathlib import Path

import pytest

from jobauto.adaptation_policy import FidelityLevel, SectionPolicy
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.cv_source import CvEntry
from jobauto.document_patch import (
    CvAdaptationPatch,
    CvFieldChange,
    CvProjectSectionChange,
    CvSkillSectionChange,
    _can_recompose_structured_section,
    apply_cv_patch,
    index_cv_source,
    merge_cv_adaptation_patch,
)


def test_adaptable_structured_section_can_be_recomposed_but_faithful_cannot() -> None:
    assert _can_recompose_structured_section(SectionPolicy(fidelity=FidelityLevel.ADAPTABLE))
    assert not _can_recompose_structured_section(
        SectionPolicy(fidelity=FidelityLevel.VERY_FAITHFUL)
    )


def _snapshot():
    project_root = Path(__file__).resolve().parents[1]
    return CandidateProfileRepository(project_root / "config" / "profiles").load_snapshot(
        project_root / "config" / "profiles" / "example" / "profile.yaml"
    )


def test_cv_source_index_uses_stable_structural_ids() -> None:
    source = _snapshot().cv_source

    first = index_cv_source(source)
    second = index_cv_source(source)

    assert first == second
    assert "headline.text" in first
    assert "summary.text" in first
    assert "experience.0.bullet.0" in first
    assert "projects.0.stack" in first
    assert "skills.0.items" in first
    assert all("alex" not in source_id.casefold() for source_id in first)


def test_patch_updates_allowed_fields_without_mutating_baseline() -> None:
    snapshot = _snapshot()
    source = snapshot.cv_source
    baseline_summary = source.summary
    baseline_bullet = source.experience[0].bullets[0]
    first_skill_group = next(iter(source.skills))
    patch = CvAdaptationPatch(
        changes=[
            CvFieldChange(
                source_id="summary.text",
                value=(
                    "Data engineer focused on reliable cloud pipelines, data quality and analytics "
                    "products, with hands-on experience translating operational requirements into "
                    "maintainable Python and SQL data flows for business users."
                ),
                fact_ids=["identity.current"],
            ),
            CvFieldChange(
                source_id="experience.0.bullet.0",
                value="Built reliable Python and SQL ingestion pipelines for operational datasets.",
                fact_ids=["identity.current"],
            ),
            CvFieldChange(
                source_id="skills.0.items",
                value=["Python 3", "SQL", "ETL/ELT", "BigQuery"],
                fact_ids=["identity.current"],
            ),
        ]
    )

    draft = apply_cv_patch(snapshot, patch)

    assert draft.document.summary.startswith("Data engineer focused on reliable cloud")
    assert draft.document.experience[0].bullets[0].startswith("Built reliable")
    assert draft.document.skills[first_skill_group] == [
        "Python 3",
        "SQL",
        "ETL/ELT",
        "BigQuery",
    ]
    assert draft.provenance["summary.text"] == ("identity.current",)
    assert source.summary == baseline_summary
    assert source.experience[0].bullets[0] == baseline_bullet


def test_patch_rejects_locked_and_unknown_surfaces() -> None:
    snapshot = _snapshot()
    with pytest.raises(ValueError, match="locked"):
        apply_cv_patch(
            snapshot,
            CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="education.0.title",
                        value="Different school",
                        fact_ids=["identity.current"],
                    )
                ]
            ),
        )

    with pytest.raises(ValueError, match="Unknown CV source ID"):
        apply_cv_patch(
            snapshot,
            CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="experience.99.title",
                        value="Unknown",
                        fact_ids=["identity.current"],
                    )
                ]
            ),
        )


def test_patch_rejects_unknown_fact_provenance() -> None:
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="Unknown candidate fact"):
        apply_cv_patch(
            snapshot,
            CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="summary.text",
                        value="Unsupported summary",
                        fact_ids=["foreign.fact"],
                    )
                ]
            ),
        )


def test_patch_can_replace_adaptable_project_and_skill_sections() -> None:
    snapshot = _snapshot()
    baseline_project_count = len(snapshot.cv_source.projects)
    patch = CvAdaptationPatch(
        projects=CvProjectSectionChange(
            entries=[
                snapshot.cv_source.projects[0],
                CvEntry(
                    title="Operational data quality workflow",
                    stack="Python, SQL, BigQuery",
                    bullets=[
                        "Built a reproducible quality-control workflow for operational datasets."
                    ],
                ),
            ],
            fact_ids=["project.energy_forecasting"],
        ),
        skills=CvSkillSectionChange(
            groups={
                "Data Engineering": ["Python", "SQL", "ETL/ELT"],
                "Cloud": ["BigQuery", "Data quality", "Monitoring"],
                "Applied Analytics": ["pandas", "scikit-learn", "MLflow"],
            },
            fact_ids=["identity.current"],
        ),
    )

    draft = apply_cv_patch(snapshot, patch)

    assert len(draft.document.projects) == 2
    assert list(draft.document.skills) == [
        "Data Engineering",
        "Cloud",
        "Applied Analytics",
    ]
    assert draft.provenance["projects.section"] == ("project.energy_forecasting",)
    assert draft.provenance["skills.section"] == ("identity.current",)
    assert len(snapshot.cv_source.projects) == baseline_project_count


def test_patch_removes_languages_duplicated_inside_skill_groups() -> None:
    snapshot = _snapshot()
    patch = CvAdaptationPatch(
        skills=CvSkillSectionChange(
            groups={
                "Data Engineering": ["Python", "SQL", "ETL/ELT"],
                "Analytics": ["pandas", "scikit-learn", "MLflow"],
                "Languages": ["French — native", "English — C1", "Spanish — B2"],
            },
            fact_ids=["identity.current"],
        )
    )

    draft = apply_cv_patch(snapshot, patch)

    assert draft.document.skills["Languages"] == ["Spanish — B2"]
    assert draft.document.skills["Data Engineering"] == ["Python", "SQL", "ETL/ELT"]


def test_focused_cv_repair_preserves_unrelated_accepted_changes() -> None:
    base = CvAdaptationPatch(
        changes=[
            CvFieldChange(
                source_id="summary.text",
                value="Accepted summary",
                fact_ids=["identity.current"],
            ),
            CvFieldChange(
                source_id="experience.0.bullet.0",
                value="Accepted experience evidence",
                fact_ids=["identity.current"],
            ),
        ],
        skills=CvSkillSectionChange(
            groups={"Data": ["Python", "SQL"]},
            fact_ids=["identity.current"],
        ),
    )
    repair = CvAdaptationPatch(
        changes=[
            CvFieldChange(
                source_id="summary.text",
                value="Repaired summary",
                fact_ids=["identity.current"],
            )
        ],
        projects=CvProjectSectionChange(
            entries=[
                CvEntry(
                    title="Relevant project",
                    stack="Python, PyTorch",
                    bullets=["Evaluated a reproducible model pipeline."],
                )
            ],
            fact_ids=["project.energy_forecasting"],
        ),
    )

    merged = merge_cv_adaptation_patch(base, repair)

    assert [change.source_id for change in merged.changes] == [
        "summary.text",
        "experience.0.bullet.0",
    ]
    assert merged.changes[0].value == "Repaired summary"
    assert merged.changes[1].value == "Accepted experience evidence"
    assert merged.skills is base.skills
    assert merged.projects is repair.projects
