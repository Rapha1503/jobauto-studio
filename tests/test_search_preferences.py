from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobauto.search_preferences import (
    RemotePreference,
    SearchOffer,
    SearchPreferences,
)


def _write_preferences(path: Path) -> Path:
    path.write_text(
        """schema_version: 1
roles:
  required: [Platform Engineer]
  preferred: [Infrastructure Engineer]
  avoid: [Team Lead]
announcement_keywords:
  required: [distributed systems]
  preferred: [high availability]
  avoid: [cold calling]
technical_stacks:
  required: [Python]
  preferred: [Kubernetes, PostgreSQL]
  avoid: [LegacySuite]
max_experience_years: 4
locations:
  required: [North Region]
  preferred: [Central District]
  avoid: [Overseas Zone]
remote: preferred
contracts:
  required: [permanent]
  preferred: [full-time]
  avoid: [internship]
max_age_days: 30
salary:
  minimum_annual: 50000
  preferred_annual: 60000
  currency: eur
sectors:
  preferred: [energy]
  avoid: [advertising]
excluded_companies: [Example Holdings]
excluded_titles: [Director]
freeform: Focus on products with measurable operational impact.
""",
        encoding="utf-8",
    )
    return path


def test_load_parses_generic_yaml_preferences(tmp_path: Path) -> None:
    preferences = SearchPreferences.load(_write_preferences(tmp_path / "search.yaml"))

    assert preferences.roles.required == ["Platform Engineer"]
    assert preferences.technical_stacks.preferred == ["Kubernetes", "PostgreSQL"]
    assert preferences.remote is RemotePreference.PREFERRED
    assert preferences.salary.currency == "EUR"
    assert preferences.freeform == "Focus on products with measurable operational impact."


def test_validation_rejects_ambiguous_and_invalid_preferences() -> None:
    with pytest.raises(ValidationError, match="multiple levels"):
        SearchPreferences.model_validate(
            {"roles": {"required": ["Engineering"], "avoid": ["engineering"]}}
        )

    with pytest.raises(ValidationError, match="preferred_annual"):
        SearchPreferences.model_validate(
            {"salary": {"minimum_annual": 60_000, "preferred_annual": 50_000}}
        )

    with pytest.raises(ValidationError, match="extra_forbidden"):
        SearchPreferences.model_validate({"preferred_cities": ["Somewhere"]})


def test_term_preferences_accept_semicolon_and_newline_separated_values() -> None:
    preferences = SearchPreferences.model_validate(
        {"roles": {"preferred": ["Financial Analyst; FP&A Analyst\nTreasury Analyst"]}}
    )

    assert preferences.roles.preferred == [
        "Financial Analyst",
        "FP&A Analyst",
        "Treasury Analyst",
    ]


def test_evaluation_rejects_only_known_hard_violations(tmp_path: Path) -> None:
    preferences = SearchPreferences.load(_write_preferences(tmp_path / "search.yaml"))
    offer = SearchOffer(
        company="Example Holdings Europe",
        title="Director of Platform Engineering",
        description="Distributed systems using Python and Kubernetes.",
        location="Overseas Zone",
        remote=False,
        contract="internship",
        experience_years=7,
        posted_at=date(2026, 4, 1),
        salary_annual=70_000,
        salary_currency="EUR",
        sector="Energy",
    )

    evaluation = preferences.evaluate(offer, today=date(2026, 7, 16))
    codes = {blocker.code for blocker in evaluation.blockers}

    assert evaluation.eligible is False
    assert evaluation.score <= 49
    assert {
        "company_excluded",
        "title_excluded",
        "locations_required_absent",
        "locations_avoided",
        "contracts_required_absent",
        "contracts_avoided",
        "experience_above_maximum",
        "offer_too_old",
    } <= codes
    assert "salary_below_minimum" not in codes


def test_demo_relaxes_experience_into_a_ranking_signal() -> None:
    preferences = SearchPreferences.model_validate({"max_experience_years": 3})
    offer = SearchOffer(
        company="Stretch Co",
        title="Data Engineer",
        experience_years=5,
    )

    evaluation = preferences.evaluate(offer, relax_experience=True)

    assert evaluation.eligible is True
    assert evaluation.blockers == []
    assert any(
        signal.criterion == "experience_years" and signal.outcome == "miss" and signal.impact < 0
        for signal in evaluation.ranking_signals
    )


def test_unknown_metadata_is_retained_instead_of_rejected() -> None:
    preferences = SearchPreferences.model_validate(
        {
            "announcement_keywords": {"required": ["event streaming"]},
            "locations": {"required": ["Preferred Area"]},
            "contracts": {"required": ["permanent"]},
            "remote": "required",
            "max_experience_years": 3,
            "max_age_days": 14,
            "salary": {"minimum_annual": 50_000, "currency": "EUR"},
            "sectors": {"required": ["mobility"]},
        }
    )
    offer = SearchOffer(company="Unknown Co", title="Platform Engineer")

    evaluation = preferences.evaluate(offer, today=date(2026, 7, 16))

    assert evaluation.eligible is True
    assert evaluation.blockers == []
    assert set(evaluation.unknown_criteria) == {
        "announcement_keywords",
        "locations",
        "contracts",
        "remote",
        "experience_years",
        "posted_at",
        "salary",
        "sectors",
    }


@pytest.mark.parametrize(
    "contract",
    ["CDI", "Permanent contract", "Open-ended contract"],
)
def test_contract_constraints_match_equivalent_labels(contract: str) -> None:
    preferences = SearchPreferences.model_validate({"contracts": {"required": ["permanent"]}})

    evaluation = preferences.evaluate(
        SearchOffer(company="Example", title="Data Engineer", contract=contract)
    )

    assert evaluation.eligible is True
    assert not evaluation.blockers


def test_full_time_without_duration_is_unknown_not_a_contract_conflict() -> None:
    preferences = SearchPreferences.model_validate(
        {"contracts": {"required": ["permanent", "fixed-term"]}}
    )

    evaluation = preferences.evaluate(
        SearchOffer(company="Example", title="Data Engineer", contract="Full time")
    )

    assert evaluation.eligible is True
    assert evaluation.blockers == []
    assert evaluation.unknown_criteria == ["contracts"]


@pytest.mark.parametrize(
    "contract",
    ["Intern (Fixed Term)", "Apprentice/Intern (Fixed Term), 6 months"],
)
def test_internship_nature_takes_priority_over_fixed_term_duration(contract: str) -> None:
    preferences = SearchPreferences.model_validate(
        {"contracts": {"required": ["permanent", "CDD"]}}
    )

    evaluation = preferences.evaluate(
        SearchOffer(company="Example", title="Data Scientist", contract=contract)
    )

    assert evaluation.eligible is False
    assert {blocker.code for blocker in evaluation.blockers} == {"contracts_required_absent"}


def test_semantic_role_and_stack_misses_are_left_for_agentic_ranking() -> None:
    preferences = SearchPreferences.model_validate(
        {
            "roles": {"required": ["Reliability Engineer"]},
            "technical_stacks": {"required": ["Go"]},
        }
    )
    offer = SearchOffer(
        company="Known Co",
        title="Product Manager",
        description="Roadmap ownership and customer research.",
    )

    evaluation = preferences.evaluate(offer)

    assert evaluation.eligible is True
    assert evaluation.blockers == []
    assert {
        signal.criterion for signal in evaluation.ranking_signals if signal.outcome == "miss"
    } == {"roles", "technical_stacks"}


def test_title_exclusions_also_cover_the_canonical_job_url() -> None:
    preferences = SearchPreferences.model_validate({"excluded_titles": ["Senior"]})

    evaluation = preferences.evaluate(
        SearchOffer(
            company="Example",
            title="Data Engineer",
            source_url="https://jobs.example.test/senior-data-engineer-123",
        )
    )

    assert evaluation.eligible is False
    assert {blocker.code for blocker in evaluation.blockers} == {"title_excluded"}


def test_soft_preferences_and_salary_only_change_ranking() -> None:
    preferences = SearchPreferences.model_validate(
        {
            "roles": {"preferred": ["Platform Engineer"], "avoid": ["Team Lead"]},
            "technical_stacks": {"preferred": ["Kubernetes"], "avoid": ["LegacySuite"]},
            "remote": "preferred",
            "salary": {"minimum_annual": 50_000, "preferred_annual": 60_000},
        }
    )
    strong = SearchOffer(
        company="A",
        title="Platform Engineer",
        description="Kubernetes services",
        remote=True,
        salary_annual=65_000,
    )
    weak = SearchOffer(
        company="B",
        title="Team Lead",
        description="LegacySuite maintenance",
        remote=False,
        salary_annual=40_000,
    )

    strong_evaluation = preferences.evaluate(strong)
    weak_evaluation = preferences.evaluate(weak)

    assert strong_evaluation.eligible is True
    assert weak_evaluation.eligible is True
    assert strong_evaluation.score > weak_evaluation.score
    assert weak_evaluation.blockers == []
    assert any(
        signal.criterion == "salary" and signal.outcome == "miss"
        for signal in weak_evaluation.ranking_signals
    )


def test_salary_with_unknown_currency_is_not_compared_or_rejected() -> None:
    preferences = SearchPreferences.model_validate(
        {"salary": {"minimum_annual": 50_000, "currency": "EUR"}}
    )
    offer = SearchOffer(company="A", title="Engineer", salary_annual=60_000)

    evaluation = preferences.evaluate(offer)

    assert evaluation.eligible is True
    assert evaluation.blockers == []
    assert evaluation.unknown_criteria == ["salary_currency"]
    assert not any(signal.criterion == "salary" for signal in evaluation.ranking_signals)


def test_semantic_stack_miss_does_not_match_partial_tokens_or_block() -> None:
    preferences = SearchPreferences.model_validate(
        {
            "announcement_keywords": {"required": ["énergie"]},
            "technical_stacks": {"required": ["SQL"]},
        }
    )
    offer = SearchOffer(
        company="C",
        title="Engineer",
        description="ENERGIE projects with NoSQL storage",
    )

    evaluation = preferences.evaluate(offer)

    assert evaluation.eligible is True
    assert evaluation.blockers == []
    assert any(
        signal.criterion == "technical_stacks" and signal.outcome == "miss"
        for signal in evaluation.ranking_signals
    )


def test_compound_terms_match_across_common_word_separators() -> None:
    preferences = SearchPreferences.model_validate(
        {"announcement_keywords": {"preferred": ["medical devices", "post market surveillance"]}}
    )
    offer = SearchOffer(
        company="Medica Europe",
        title="Regulatory Affairs Specialist",
        description="Medical-device compliance and post-market surveillance activities.",
    )

    evaluation = preferences.evaluate(offer)

    signal = next(
        item for item in evaluation.ranking_signals if item.criterion == "announcement_keywords"
    )
    assert signal.outcome == "match"
    assert signal.terms == ["medical devices", "post market surveillance"]
