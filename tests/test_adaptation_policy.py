from __future__ import annotations

from pathlib import Path

import pytest

from jobauto.adaptation_policy import (
    AdaptationPolicy,
    CvLayoutPolicy,
    FidelityLevel,
    validate_section_change,
)


def test_default_cv_layout_never_shrinks_below_ten_points() -> None:
    policy = CvLayoutPolicy()

    assert policy.minimum_font_size_pt == 10.0
    assert policy.maximum_font_size_pt == 12.0


def test_cv_layout_rejects_inverted_readability_bounds() -> None:
    with pytest.raises(ValueError, match="minimum_font_size_pt"):
        CvLayoutPolicy(minimum_font_size_pt=11, maximum_font_size_pt=10)

    with pytest.raises(ValueError, match="minimum_line_height_ratio"):
        CvLayoutPolicy(minimum_line_height_ratio=1.3, maximum_line_height_ratio=1.1)


def _write_policy(path: Path) -> Path:
    path.write_text(
        """schema_version: 1
policy_id: example-cv
documents:
  cv:
    section_order: [identity, summary, experience, projects, skills]
    sections:
      identity:
        fidelity: locked
        required: true
        protected_terms: [Alex Morgan]
      summary:
        fidelity: adaptable
        required: true
        target_lines: 4
        min_characters: 240
        max_characters: 520
        protected_fact_ids: [identity.current]
      experience:
        fidelity: very_faithful
        required: true
      projects:
        fidelity: highly_adaptable
        required: true
      skills:
        fidelity: replaceable
        required: true
  letter:
    section_order: [body]
    sections:
      body:
        fidelity: highly_adaptable
        required: true
        target_lines: 35
        max_characters: 2600
""",
        encoding="utf-8",
    )
    return path


def test_policy_loads_and_hashes_stably(tmp_path: Path) -> None:
    path = _write_policy(tmp_path / "policy.yaml")

    first = AdaptationPolicy.load(path)
    second = AdaptationPolicy.load(path)

    assert first.documents["cv"].sections["summary"].fidelity is FidelityLevel.ADAPTABLE
    assert first.policy_hash == second.policy_hash
    assert len(first.policy_hash) == 64


def test_policy_requires_section_order_to_match_sections(tmp_path: Path) -> None:
    path = _write_policy(tmp_path / "policy.yaml")
    text = path.read_text(encoding="utf-8").replace(
        "section_order: [identity, summary, experience, projects, skills]",
        "section_order: [identity, summary]",
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="section_order"):
        AdaptationPolicy.load(path)


@pytest.mark.parametrize(
    ("fidelity", "expected_capabilities"),
    [
        (
            FidelityLevel.LOCKED,
            {"shorten": False, "rephrase": False, "reorder": False, "replace": False},
        ),
        (
            FidelityLevel.VERY_FAITHFUL,
            {"shorten": True, "rephrase": True, "reorder": False, "replace": False},
        ),
        (
            FidelityLevel.ADAPTABLE,
            {"shorten": True, "rephrase": True, "reorder": True, "replace": False},
        ),
        (
            FidelityLevel.HIGHLY_ADAPTABLE,
            {"shorten": True, "rephrase": True, "reorder": True, "replace": True},
        ),
        (
            FidelityLevel.REPLACEABLE,
            {"shorten": True, "rephrase": True, "reorder": True, "replace": True},
        ),
    ],
)
def test_section_capabilities_are_derived_from_fidelity(
    fidelity: FidelityLevel, expected_capabilities: dict[str, bool]
) -> None:
    policy = AdaptationPolicy.model_validate(
        {
            "policy_id": "capabilities",
            "documents": {
                "cv": {
                    "section_order": ["summary"],
                    "sections": {"summary": {"fidelity": fidelity}},
                }
            },
        }
    )

    section = policy.documents["cv"].sections["summary"]

    assert section.capabilities.model_dump() == expected_capabilities


def test_section_serialization_persists_no_edit_permission_booleans(tmp_path: Path) -> None:
    policy = AdaptationPolicy.load(_write_policy(tmp_path / "policy.yaml"))
    section = policy.documents["cv"].sections["summary"]

    assert not {key for key in section.model_dump() if key.startswith("allow_")}
    assert section.capabilities.reorder is True


def test_legacy_permission_booleans_are_rejected() -> None:
    with pytest.raises(ValueError, match="allow_replace"):
        AdaptationPolicy.model_validate(
            {
                "policy_id": "legacy-permissions",
                "documents": {
                    "cv": {
                        "section_order": ["summary"],
                        "sections": {
                            "summary": {
                                "fidelity": "adaptable",
                                "allow_replace": True,
                            }
                        },
                    }
                },
            }
        )


def test_change_validation_protects_locked_and_required_content(tmp_path: Path) -> None:
    policy = AdaptationPolicy.load(_write_policy(tmp_path / "policy.yaml"))
    identity = policy.documents["cv"].sections["identity"]

    violations = validate_section_change(identity, "Alex Morgan", "Alex M.")

    assert {item.code for item in violations} == {
        "locked_content_changed",
        "protected_term_missing",
    }


def test_change_validation_checks_budget_and_fact_provenance(tmp_path: Path) -> None:
    policy = AdaptationPolicy.load(_write_policy(tmp_path / "policy.yaml"))
    summary = policy.documents["cv"].sections["summary"]

    violations = validate_section_change(
        summary,
        "Original summary",
        "Short adapted summary",
        used_fact_ids=[],
    )

    assert {item.code for item in violations} == {
        "below_min_characters",
        "protected_fact_missing",
    }


def test_policy_preserves_structural_and_protection_constraints(tmp_path: Path) -> None:
    policy = AdaptationPolicy.load(_write_policy(tmp_path / "policy.yaml"))
    cv_policy = policy.documents["cv"]
    summary = cv_policy.sections["summary"]

    assert cv_policy.section_order == ["identity", "summary", "experience", "projects", "skills"]
    assert summary.required is True
    assert summary.target_lines == 4
    assert summary.min_characters == 240
    assert summary.max_characters == 520
    assert summary.protected_fact_ids == ["identity.current"]
