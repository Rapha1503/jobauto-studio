from __future__ import annotations

import re
import unicodedata
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RemotePreference(StrEnum):
    NEUTRAL = "neutral"
    REQUIRED = "required"
    PREFERRED = "preferred"
    AVOID = "avoid"


class TermPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    required: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    @field_validator("required", "preferred", "avoid")
    @classmethod
    def clean_terms(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            for term in re.split(r"[;\r\n]+", value):
                compact = " ".join(term.split())
                if not compact:
                    continue
                key = _normalize(compact)
                if key not in seen:
                    cleaned.append(compact)
                    seen.add(key)
        if values and not cleaned:
            raise ValueError("preference terms cannot be blank")
        return cleaned

    @model_validator(mode="after")
    def levels_do_not_overlap(self) -> TermPreferences:
        levels = [
            {_normalize(value) for value in values}
            for values in (self.required, self.preferred, self.avoid)
        ]
        overlaps = (levels[0] & levels[1]) | (levels[0] & levels[2]) | (levels[1] & levels[2])
        if overlaps:
            raise ValueError(
                "preference terms cannot appear at multiple levels: " + ", ".join(sorted(overlaps))
            )
        return self


class SalaryPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_annual: int | None = Field(default=None, ge=0)
    preferred_annual: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None

    @model_validator(mode="after")
    def preferred_salary_respects_minimum(self) -> SalaryPreferences:
        if (
            self.minimum_annual is not None
            and self.preferred_annual is not None
            and self.preferred_annual < self.minimum_annual
        ):
            raise ValueError("preferred_annual cannot be below minimum_annual")
        return self


class SearchOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    description: str | None = None
    location: str | None = Field(default=None, max_length=500)
    remote: bool | None = None
    contract: str | None = Field(default=None, max_length=200)
    experience_years: float | None = Field(default=None, ge=0, le=100)
    posted_at: date | None = None
    salary_annual: int | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, min_length=3, max_length=3)
    sector: str | None = Field(default=None, max_length=300)

    @field_validator(
        "company",
        "title",
        "source_url",
        "description",
        "location",
        "contract",
        "sector",
        mode="before",
    )
    @classmethod
    def compact_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        compact = " ".join(value.split())
        return compact or None

    @field_validator("salary_currency")
    @classmethod
    def normalize_salary_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else None


class EvaluationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    criterion: str
    message: str
    terms: list[str] = Field(default_factory=list)


class RankingSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion: str
    level: Literal["required", "preferred", "avoid", "constraint"]
    outcome: Literal["match", "miss", "unknown"]
    impact: int
    terms: list[str] = Field(default_factory=list)


class SearchEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eligible: bool
    score: int = Field(ge=0, le=100)
    blockers: list[EvaluationFinding] = Field(default_factory=list)
    ranking_signals: list[RankingSignal] = Field(default_factory=list)
    unknown_criteria: list[str] = Field(default_factory=list)


class SearchPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    roles: TermPreferences = Field(default_factory=TermPreferences)
    announcement_keywords: TermPreferences = Field(default_factory=TermPreferences)
    technical_stacks: TermPreferences = Field(default_factory=TermPreferences)
    max_experience_years: float | None = Field(default=None, ge=0, le=100)
    locations: TermPreferences = Field(default_factory=TermPreferences)
    remote: RemotePreference = RemotePreference.NEUTRAL
    contracts: TermPreferences = Field(default_factory=TermPreferences)
    max_age_days: int | None = Field(default=None, ge=0, le=3650)
    salary: SalaryPreferences = Field(default_factory=SalaryPreferences)
    sectors: TermPreferences = Field(default_factory=TermPreferences)
    excluded_companies: list[str] = Field(default_factory=list)
    excluded_titles: list[str] = Field(default_factory=list)
    freeform: str | None = Field(default=None, max_length=10_000)

    @field_validator("excluded_companies", "excluded_titles")
    @classmethod
    def clean_exclusions(cls, values: list[str]) -> list[str]:
        return TermPreferences.clean_terms(values)

    @field_validator("freeform")
    @classmethod
    def clean_freeform(cls, value: str | None) -> str | None:
        compact = " ".join(value.split()) if value else ""
        return compact or None

    @classmethod
    def load(cls, path: Path) -> SearchPreferences:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    def evaluate(self, offer: SearchOffer, *, today: date | None = None) -> SearchEvaluation:
        blockers: list[EvaluationFinding] = []
        signals: list[RankingSignal] = []
        unknowns: list[str] = []
        searchable = (
            _known_text(offer.title, offer.description) if offer.description is not None else None
        )

        dimensions = (
            ("roles", self.roles, offer.title, False),
            ("announcement_keywords", self.announcement_keywords, searchable, False),
            ("technical_stacks", self.technical_stacks, searchable, False),
            ("locations", self.locations, offer.location, True),
            ("contracts", self.contracts, offer.contract, True),
            ("sectors", self.sectors, offer.sector, False),
        )
        for criterion, preferences, evidence, hard_constraint in dimensions:
            _evaluate_terms(
                criterion,
                preferences,
                evidence,
                hard_constraint=hard_constraint,
                blockers=blockers,
                signals=signals,
                unknowns=unknowns,
            )

        _explicit_exclusions(self, offer, blockers)
        _remote_policy(self.remote, offer.remote, blockers, signals, unknowns)
        _maximum_constraint(
            criterion="experience_years",
            actual=offer.experience_years,
            maximum=self.max_experience_years,
            blocker_code="experience_above_maximum",
            blockers=blockers,
            signals=signals,
            unknowns=unknowns,
        )
        age = (
            None
            if offer.posted_at is None
            else max(0, ((today or date.today()) - offer.posted_at).days)
        )
        _maximum_constraint(
            criterion="posted_at",
            actual=age,
            maximum=self.max_age_days,
            blocker_code="offer_too_old",
            blockers=blockers,
            signals=signals,
            unknowns=unknowns,
        )
        _salary_signal(self.salary, offer, signals, unknowns)

        score = max(0, min(100, 50 + sum(signal.impact for signal in signals)))
        if blockers:
            score = min(score, 49)
        return SearchEvaluation(
            eligible=not blockers,
            score=score,
            blockers=blockers,
            ranking_signals=signals,
            unknown_criteria=list(dict.fromkeys(unknowns)),
        )


def _evaluate_terms(
    criterion: str,
    preferences: TermPreferences,
    evidence: str | None,
    *,
    hard_constraint: bool,
    blockers: list[EvaluationFinding],
    signals: list[RankingSignal],
    unknowns: list[str],
) -> None:
    if evidence is None:
        if preferences.required or preferences.preferred or preferences.avoid:
            unknowns.append(criterion)
        return

    if criterion == "contracts":
        configured_terms = [
            *preferences.required,
            *preferences.preferred,
            *preferences.avoid,
        ]
        if not _contract_categories(evidence) and not _matching_terms(
            configured_terms,
            evidence,
        ):
            unknowns.append(criterion)
            return

    for level, impact in (("required", 6), ("preferred", 3), ("avoid", -4)):
        configured = getattr(preferences, level)
        if not configured:
            continue
        matches = (
            _matching_contract_terms(configured, evidence)
            if criterion == "contracts"
            else _matching_terms(configured, evidence)
        )
        outcome = "match" if matches else "miss"
        signals.append(
            RankingSignal(
                criterion=criterion,
                level=level,
                outcome=outcome,
                impact=(impact * max(1, len(matches)))
                if matches
                else (0 if level != "required" else -4),
                terms=matches or configured,
            )
        )
        violation = (level == "required" and not matches) or (level == "avoid" and matches)
        if hard_constraint and violation:
            blockers.append(
                EvaluationFinding(
                    code=f"{criterion}_{'required_absent' if level == 'required' else 'avoided'}",
                    criterion=criterion,
                    message=f"Known {criterion} conflicts with the configured search constraint.",
                    terms=configured if not matches else matches,
                )
            )


def _explicit_exclusions(
    preferences: SearchPreferences,
    offer: SearchOffer,
    blockers: list[EvaluationFinding],
) -> None:
    for criterion, configured, evidence in (
        ("company", preferences.excluded_companies, offer.company),
        ("title", preferences.excluded_titles, _known_text(offer.title, offer.source_url)),
    ):
        matches = _matching_terms(configured, evidence)
        if matches:
            blockers.append(
                EvaluationFinding(
                    code=f"{criterion}_excluded",
                    criterion=criterion,
                    message=f"The {criterion} matches an explicit exclusion.",
                    terms=matches,
                )
            )


def _matching_contract_terms(configured: list[str], evidence: str) -> list[str]:
    direct = set(_matching_terms(configured, evidence))
    evidence_categories = _contract_categories(evidence)
    return [
        term
        for term in configured
        if term in direct or bool(_contract_categories(term) & evidence_categories)
    ]


def _contract_categories(value: str) -> set[str]:
    normalized = _normalize(value)
    groups = {
        "permanent": (
            "cdi",
            "permanent",
            "indefinite contract",
            "open ended contract",
            "open-ended contract",
        ),
        "fixed_term": ("cdd", "fixed term", "temporary contract"),
        "internship": ("stage", "internship", "intern"),
        "apprenticeship": ("alternance", "apprenticeship", "work study"),
        "freelance": ("freelance", "contractor", "independent contractor"),
    }
    matched = {
        category
        for category, aliases in groups.items()
        if any(_matching_terms([alias], normalized) for alias in aliases)
    }
    engagement_types = matched & {"internship", "apprenticeship", "freelance"}
    return engagement_types or matched


def _remote_policy(
    preference: RemotePreference,
    actual: bool | None,
    blockers: list[EvaluationFinding],
    signals: list[RankingSignal],
    unknowns: list[str],
) -> None:
    if preference is RemotePreference.NEUTRAL:
        return
    if actual is None:
        unknowns.append("remote")
        return
    matches = actual if preference is not RemotePreference.AVOID else not actual
    signals.append(
        RankingSignal(
            criterion="remote",
            level=preference.value,
            outcome="match" if matches else "miss",
            impact=4 if matches else -4,
        )
    )
    if preference in {RemotePreference.REQUIRED, RemotePreference.AVOID} and not matches:
        blockers.append(
            EvaluationFinding(
                code="remote_policy_violated",
                criterion="remote",
                message="The known remote arrangement conflicts with the search policy.",
            )
        )


def _maximum_constraint(
    *,
    criterion: str,
    actual: float | int | None,
    maximum: float | int | None,
    blocker_code: str,
    blockers: list[EvaluationFinding],
    signals: list[RankingSignal],
    unknowns: list[str],
) -> None:
    if maximum is None:
        return
    if actual is None:
        unknowns.append(criterion)
        return
    within_limit = actual <= maximum
    signals.append(
        RankingSignal(
            criterion=criterion,
            level="constraint",
            outcome="match" if within_limit else "miss",
            impact=3 if within_limit else -12,
        )
    )
    if not within_limit:
        blockers.append(
            EvaluationFinding(
                code=blocker_code,
                criterion=criterion,
                message=f"Known {criterion} value {actual:g} exceeds maximum {maximum:g}.",
            )
        )


def _salary_signal(
    preferences: SalaryPreferences,
    offer: SearchOffer,
    signals: list[RankingSignal],
    unknowns: list[str],
) -> None:
    thresholds = [
        ("required", preferences.minimum_annual),
        ("preferred", preferences.preferred_annual),
    ]
    if not any(value is not None for _level, value in thresholds):
        return
    if offer.salary_annual is None:
        unknowns.append("salary")
        return
    if preferences.currency and offer.salary_currency != preferences.currency:
        unknowns.append("salary_currency")
        return
    for level, threshold in thresholds:
        if threshold is not None:
            matches = offer.salary_annual >= threshold
            signals.append(
                RankingSignal(
                    criterion="salary",
                    level=level,
                    outcome="match" if matches else "miss",
                    impact=3 if matches else -3,
                )
            )


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    accentless = "".join(char for char in decomposed if not unicodedata.combining(char))
    # Job ads and candidate preferences frequently alternate between spaces,
    # hyphens and slashes for the same compound term. Keep meaningful symbols
    # such as C++, C# and .NET intact while normalizing word separators.
    separated = re.sub(r"[-‐‑‒–—_/]+", " ", accentless)
    return " ".join(separated.split())


def _matching_terms(terms: list[str], evidence: str) -> list[str]:
    normalized = _match_key(evidence)
    return [
        term
        for term in terms
        if re.search(rf"(?<!\w){re.escape(_match_key(term))}(?!\w)", normalized)
    ]


def _match_key(value: str) -> str:
    tokens = _normalize(value).split()
    return " ".join(
        token[:-1]
        if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is"))
        else token
        for token in tokens
    )


def _known_text(*values: str | None) -> str | None:
    known = [value for value in values if value]
    return " ".join(known) if known else None
