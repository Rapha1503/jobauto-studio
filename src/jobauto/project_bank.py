from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from jobauto.adaptation_policy import FidelityLevel
from jobauto.models import OfferAnalysis, RoleFamily
from jobauto.yaml_utils import safe_load_bounded

PROJECT_ID_PATTERN = re.compile(r"^[a-z0-9_]{2,80}$")


class ProjectStatus(StrEnum):
    VERIFIED_PUBLIC = "verified_public"
    VALIDATED_PRIVATE = "validated_private"
    CANDIDATE_REVIEW = "candidate_review"
    DO_NOT_USE_DEFAULT = "do_not_use_default"


class ProjectVisibility(StrEnum):
    CV_PROJECT = "cv_project"
    CONTEXT = "context"
    HIDDEN = "hidden"


class ProjectBankEntry(BaseModel):
    id: str = Field(min_length=2, max_length=80, pattern=PROJECT_ID_PATTERN.pattern)
    title: str = Field(min_length=3, max_length=140)
    status: ProjectStatus
    visibility: ProjectVisibility
    role_fit: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    verified_stack: list[str] = Field(default_factory=list)
    transferable_keywords: list[str] = Field(default_factory=list)
    default_stack_line: str = Field(default="", max_length=220)
    cv_bullets: list[str] = Field(default_factory=list)
    letter_angles: list[str] = Field(default_factory=list)
    use_mode: str = "reframe"
    title_fidelity: FidelityLevel = FidelityLevel.VERY_FAITHFUL
    stack_fidelity: FidelityLevel = FidelityLevel.ADAPTABLE
    description_fidelity: FidelityLevel = FidelityLevel.ADAPTABLE
    avoid_if: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator(
        "role_fit",
        "keywords",
        "verified_stack",
        "transferable_keywords",
        "cv_bullets",
        "letter_angles",
        "avoid_if",
        "warnings",
    )
    @classmethod
    def list_items_are_compact(cls, values: list[str]) -> list[str]:
        compact = [value.strip() for value in values if value.strip()]
        if len(compact) != len(values):
            raise ValueError("project bank lists cannot contain empty items")
        for value in compact:
            if "\n" in value:
                raise ValueError("project bank list items must stay single-line")
        return compact

    @property
    def cv_eligible(self) -> bool:
        return (
            self.visibility is ProjectVisibility.CV_PROJECT
            and self.status is not ProjectStatus.DO_NOT_USE_DEFAULT
        )

    @property
    def context_eligible(self) -> bool:
        return (
            self.visibility is ProjectVisibility.CONTEXT
            and self.status is not ProjectStatus.DO_NOT_USE_DEFAULT
        )


@dataclass(frozen=True)
class ProjectBankSelection:
    cv_projects: list[ProjectBankEntry]
    context_projects: list[ProjectBankEntry]
    warnings: list[str]


class ProjectBank:
    def __init__(self, entries: list[ProjectBankEntry]) -> None:
        ids = [entry.id for entry in entries]
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            raise ValueError(f"duplicate project ids: {duplicates}")
        self.entries = tuple(entries)
        self._by_id = {entry.id: entry for entry in entries}

    @classmethod
    def load(cls, path: Path) -> ProjectBank:
        data = safe_load_bounded(path)
        entries = [ProjectBankEntry(**item) for item in data.get("projects", [])]
        return cls(entries)

    def get(self, project_id: str) -> ProjectBankEntry:
        return self._by_id[project_id]

    def select(
        self,
        profile: OfferAnalysis,
        offer_text: str,
        *,
        cv_limit: int = 4,
        context_limit: int = 4,
    ) -> ProjectBankSelection:
        scored = [
            (self._score(entry, profile, offer_text), index, entry)
            for index, entry in enumerate(self.entries)
            if self._score(entry, profile, offer_text) > -100
        ]
        scored.sort(key=lambda item: (-item[0], item[1], item[2].id))

        context_projects = [
            entry for score, _index, entry in scored if entry.context_eligible and score > 0
        ][:context_limit]
        cv_projects = [entry for score, _index, entry in scored if entry.cv_eligible and score > 0][
            :cv_limit
        ]

        warnings = []
        for entry in [*context_projects, *cv_projects]:
            if entry.status in {ProjectStatus.VALIDATED_PRIVATE, ProjectStatus.CANDIDATE_REVIEW}:
                warnings.append(
                    f"{entry.title}: {entry.status.value}, verifier la formulation avant envoi."
                )
            warnings.extend(entry.warnings)

        return ProjectBankSelection(
            cv_projects=cv_projects,
            context_projects=context_projects,
            warnings=_unique(warnings),
        )

    def _score(self, entry: ProjectBankEntry, profile: OfferAnalysis, offer_text: str) -> int:
        if entry.status is ProjectStatus.DO_NOT_USE_DEFAULT:
            return -1000
        haystack = _normalize_text(
            " ".join(
                [
                    offer_text,
                    profile.role,
                    profile.normalized_role,
                    profile.summary,
                    " ".join(profile.required_skills),
                    " ".join(profile.preferred_skills),
                    " ".join(profile.targeted_keywords),
                ]
            )
        )
        if any(_normalize_text(term) in haystack for term in entry.avoid_if):
            return -1000

        role_keys = _role_keys(profile)
        score = 0
        for role in entry.role_fit:
            if _normalize_key(role) in role_keys:
                score += 10
        for keyword in entry.keywords:
            normalized = _normalize_text(keyword)
            if normalized and normalized in haystack:
                score += 3
        for keyword in entry.transferable_keywords:
            normalized = _normalize_text(keyword)
            if normalized and normalized in haystack:
                score += 1
        if score == 0:
            return 0
        if entry.status is ProjectStatus.VERIFIED_PUBLIC:
            score += 2
        elif entry.status is ProjectStatus.VALIDATED_PRIVATE:
            score -= 1
        elif entry.status is ProjectStatus.CANDIDATE_REVIEW:
            score -= 3
        return max(score, 1)


def format_project_bank_selection(selection: ProjectBankSelection) -> str:
    lines = [
        "Contexte projets utilisable par les agents. Les projets contextuels peuvent orienter l'experience, le resume ou la lettre sans etre affiches dans la section Projets.",
        "",
        "CV project candidates:",
    ]
    for entry in selection.cv_projects:
        lines.extend(_format_entry(entry))
    lines.extend(["", "Context projects:"])
    for entry in selection.context_projects:
        lines.extend(_format_entry(entry))
    if selection.warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in selection.warnings)
    return "\n".join(lines)


def _format_entry(entry: ProjectBankEntry) -> list[str]:
    return [
        f"- id={entry.id}; status={entry.status.value}; visibility={entry.visibility.value}; title={entry.title}",
        f"  stack={entry.default_stack_line}",
        f"  role_fit={', '.join(entry.role_fit)}",
        f"  keywords={', '.join(entry.keywords)}",
        "  cv_bullets=" + " || ".join(entry.cv_bullets[:3]),
        "  letter_angles=" + " || ".join(entry.letter_angles[:3]),
    ]


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9+/.# -]+", " ", ascii_value)


def _normalize_key(value: str | RoleFamily) -> str:
    text = value.value if isinstance(value, RoleFamily) else value
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _normalize_text(text))).strip("_")


def _role_keys(profile: OfferAnalysis) -> set[str]:
    keys = {_normalize_key(profile.role_family), _normalize_key(profile.normalized_role)}
    keys.add(_normalize_key(profile.role))
    if "ai" in keys or "ia" in keys:
        keys.add("ai_engineer")
    return {key for key in keys if key}


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
