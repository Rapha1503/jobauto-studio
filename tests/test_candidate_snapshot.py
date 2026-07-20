from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from jobauto.candidate_snapshot import CandidateProfileRepository


def write_synthetic_profile(
    root: Path,
    *,
    cv_name: str = "Alex Morgan",
    phone_label: str = "Phone",
    projects: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "facts.yaml").write_text(
        "facts:\n"
        "  - fact_id: identity.current\n"
        "    claim: Alex Morgan is a data engineer.\n"
        "    status: verified\n",
        encoding="utf-8",
    )
    (root / "projects.yaml").write_text(
        projects
        if projects is not None
        else """projects:
  - id: energy_forecasting
    title: Energy demand forecasting
    status: verified_public
    visibility: cv_project
    role_fit: [data_engineer]
    keywords: [Python, forecasting]
    verified_stack: [Python, pandas, scikit-learn, MLflow]
    default_stack_line: Python, pandas, scikit-learn, MLflow
    cv_bullets: [Built a forecasting workflow.]
    letter_angles: [Connect forecasting work to the role.]
""",
        encoding="utf-8",
    )
    (root / "skills.yaml").write_text(
        """minimum_group_overlap: 0.75
verified:
  Data Engineering: [Python, SQL, ETL/ELT, BigQuery]
  Machine Learning: [pandas, scikit-learn, MLflow]
transferable: {}
""",
        encoding="utf-8",
    )
    (root / "cv_model.tex").write_text("CV MODEL\n", encoding="utf-8")
    (root / "cv_source.md").write_text(
        f"""# {cv_name}
Data Engineer | Python, SQL, Cloud | Toulouse
Email: alex.morgan@example.test | {phone_label}: +33 1 00 00 00 00

## Projects
### Energy demand forecasting | Python, pandas, scikit-learn, MLflow
- Built a time-series training workflow.

## Skills
Data Engineering: Python, SQL, ETL/ELT, BigQuery
Machine Learning: pandas, scikit-learn, MLflow
""",
        encoding="utf-8",
    )
    (root / "letter_model.txt").write_text("LETTER MODEL\n", encoding="utf-8")
    (root / "adaptation_policy.yaml").write_text(
        """policy_id: test
documents:
  cv:
    section_order: [identity, projects, skills]
    sections:
      identity:
        fidelity: locked
        protected_terms: [Alex Morgan]
      projects:
        fidelity: highly_adaptable
      skills:
        fidelity: replaceable
""",
        encoding="utf-8",
    )
    profile_path = root / "profile.yaml"
    profile_path.write_text(
        """candidate_id: alex-morgan
identity:
  first_name: Alex
  last_name: Morgan
  email: alex.morgan@example.test
  phone: "+33 1 00 00 00 00"
facts_path: facts.yaml
project_bank_path: projects.yaml
skill_policy_path: skills.yaml
cv_model_path: cv_model.tex
cv_source_path: cv_source.md
letter_model_path: letter_model.txt
adaptation_policy_path: adaptation_policy.yaml
protected_claims: [identity.current]
""",
        encoding="utf-8",
    )
    return profile_path


def test_example_profile_loads_one_validated_snapshot() -> None:
    project_root = Path(__file__).resolve().parents[1]
    repository = CandidateProfileRepository(project_root / "config" / "profiles")

    snapshot = repository.load_snapshot(
        project_root / "config" / "profiles" / "example" / "profile.yaml"
    )

    assert snapshot.profile.candidate_id == "alex-morgan"
    assert snapshot.facts.require("identity.current").claim
    assert snapshot.project_bank.entries
    assert snapshot.project_bank.entries[0].title == snapshot.cv_source.projects[0].title
    assert "JOBAUTO_BODY" in snapshot.cv_template
    assert "Dear hiring team" in snapshot.letter_reference
    assert snapshot.skill_policy.verified_groups
    assert snapshot.search_preferences.max_experience_years == 3
    assert snapshot.submission_preferences.max_applications_per_campaign == 5
    assert snapshot.snapshot_hash
    assert set(snapshot.asset_hashes) == {
        "adaptation_policy",
        "cv_model",
        "cv_source",
        "facts",
        "letter_model",
        "profile",
        "project_bank",
        "search_preferences",
        "skill_policy",
        "submission_preferences",
    }


def test_snapshot_rejects_identity_conflict(tmp_path: Path) -> None:
    profile_path = write_synthetic_profile(tmp_path, cv_name="Foreign Person")

    with pytest.raises(ValueError, match="identity"):
        CandidateProfileRepository(tmp_path).load_snapshot(profile_path)


def test_snapshot_accepts_localized_phone_label(tmp_path: Path) -> None:
    profile_path = write_synthetic_profile(tmp_path, phone_label="Téléphone")

    snapshot = CandidateProfileRepository(tmp_path).load_snapshot(profile_path)

    assert snapshot.profile.identity.phone == "+33 1 00 00 00 00"


def test_snapshot_rejects_empty_canonical_store(tmp_path: Path) -> None:
    profile_path = write_synthetic_profile(tmp_path, projects="projects: []\n")

    with pytest.raises(ValueError, match="missing from project bank"):
        CandidateProfileRepository(tmp_path).load_snapshot(profile_path)


def test_snapshot_rejects_assets_outside_the_profiles_root(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    profile_path = write_synthetic_profile(profiles_root / "alex")
    escaped_facts = tmp_path / "facts.yaml"
    escaped_facts.write_text((profile_path.parent / "facts.yaml").read_text(encoding="utf-8"))
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "facts_path: facts.yaml", "facts_path: ../../facts.yaml"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="profiles root"):
        CandidateProfileRepository(profiles_root).load_snapshot(profile_path)


def test_snapshot_hashes_are_stable_and_track_exact_assets(tmp_path: Path) -> None:
    profile_path = write_synthetic_profile(tmp_path)
    repository = CandidateProfileRepository(tmp_path)

    first = repository.load_snapshot(profile_path)
    second = repository.load_snapshot(profile_path)
    (tmp_path / "facts.yaml").write_text(
        "facts:\n"
        "  - fact_id: identity.current\n"
        "    claim: Alex Morgan is an analytics engineer.\n"
        "    status: verified\n",
        encoding="utf-8",
    )
    changed = repository.load_snapshot(profile_path)

    assert first.asset_hashes == second.asset_hashes
    assert first.snapshot_hash == second.snapshot_hash
    assert changed.asset_hashes["facts"] != first.asset_hashes["facts"]
    assert changed.snapshot_hash != first.snapshot_hash


def test_snapshot_is_immutable(tmp_path: Path) -> None:
    snapshot = CandidateProfileRepository(tmp_path).load_snapshot(write_synthetic_profile(tmp_path))
    project_bank = snapshot.project_bank

    with pytest.raises(FrozenInstanceError):
        snapshot.snapshot_hash = "changed"
    project_bank.entries[0].title = "Mutated outside the snapshot"

    assert isinstance(snapshot.project_bank.entries, tuple)
    assert snapshot.project_bank.entries[0].title == "Energy demand forecasting"


def test_unapproved_fact_is_not_exposed_as_agent_evidence(tmp_path: Path) -> None:
    profile_path = write_synthetic_profile(tmp_path)
    facts_path = tmp_path / "facts.yaml"
    facts_path.write_text(
        facts_path.read_text(encoding="utf-8")
        + "  - fact_id: claim.to.review\n"
        + "    claim: Candidate may know an unconfirmed technology.\n"
        + "    status: unverified\n",
        encoding="utf-8",
    )

    snapshot = CandidateProfileRepository(tmp_path).load_snapshot(profile_path)

    assert "claim.to.review" not in snapshot.evidence_ids
    with pytest.raises(KeyError, match="Unknown candidate fact or evidence"):
        snapshot.require_evidence_ids(["claim.to.review"])
