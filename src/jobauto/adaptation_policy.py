from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class FidelityLevel(StrEnum):
    LOCKED = "locked"
    VERY_FAITHFUL = "very_faithful"
    ADAPTABLE = "adaptable"
    HIGHLY_ADAPTABLE = "highly_adaptable"
    REPLACEABLE = "replaceable"


class SectionCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    shorten: bool
    rephrase: bool
    reorder: bool
    replace: bool


_CAPABILITIES_BY_FIDELITY = {
    FidelityLevel.LOCKED: SectionCapabilities(
        shorten=False, rephrase=False, reorder=False, replace=False
    ),
    FidelityLevel.VERY_FAITHFUL: SectionCapabilities(
        shorten=True, rephrase=True, reorder=False, replace=False
    ),
    FidelityLevel.ADAPTABLE: SectionCapabilities(
        shorten=True, rephrase=True, reorder=True, replace=False
    ),
    FidelityLevel.HIGHLY_ADAPTABLE: SectionCapabilities(
        shorten=True, rephrase=True, reorder=True, replace=True
    ),
    FidelityLevel.REPLACEABLE: SectionCapabilities(
        shorten=True, rephrase=True, reorder=True, replace=True
    ),
}


class SectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fidelity: FidelityLevel
    required: bool = True
    target_lines: int | None = Field(default=None, ge=1, le=200)
    min_characters: int | None = Field(default=None, ge=0, le=20_000)
    max_characters: int | None = Field(default=None, ge=1, le=20_000)
    protected_terms: list[str] = Field(default_factory=list)
    protected_fact_ids: list[str] = Field(default_factory=list)

    @property
    def capabilities(self) -> SectionCapabilities:
        return _CAPABILITIES_BY_FIDELITY[self.fidelity]

    @model_validator(mode="after")
    def transformation_contract_is_consistent(self) -> SectionPolicy:
        if (
            self.min_characters is not None
            and self.max_characters is not None
            and self.min_characters > self.max_characters
        ):
            raise ValueError("min_characters cannot exceed max_characters")
        return self


class CvLayoutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    minimum_font_size_pt: float = Field(default=10.0, ge=9.0, le=12.0)
    maximum_font_size_pt: float = Field(default=12.0, ge=9.0, le=12.0)
    minimum_line_height_ratio: float = Field(default=1.10, ge=1.0, le=1.5)
    maximum_line_height_ratio: float = Field(default=1.50, ge=1.0, le=1.5)

    @model_validator(mode="after")
    def readable_range_is_consistent(self) -> CvLayoutPolicy:
        if self.minimum_font_size_pt > self.maximum_font_size_pt:
            raise ValueError("minimum_font_size_pt cannot exceed maximum_font_size_pt")
        if self.minimum_line_height_ratio > self.maximum_line_height_ratio:
            raise ValueError("minimum_line_height_ratio cannot exceed maximum_line_height_ratio")
        return self


class DocumentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_order: list[str] = Field(min_length=1)
    sections: dict[str, SectionPolicy] = Field(min_length=1)
    layout: CvLayoutPolicy | None = None

    @model_validator(mode="after")
    def section_order_matches_sections(self) -> DocumentPolicy:
        if len(self.section_order) != len(set(self.section_order)):
            raise ValueError("section_order cannot contain duplicates")
        if set(self.section_order) != set(self.sections):
            raise ValueError("section_order must contain every section exactly once")
        return self


class AdaptationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    policy_id: str = Field(min_length=1, max_length=100)
    documents: dict[str, DocumentPolicy] = Field(min_length=1)

    @classmethod
    def load(cls, path: Path) -> AdaptationPolicy:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(data)

    @property
    def policy_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PolicyViolation(BaseModel):
    code: str
    message: str


def validate_section_change(
    policy: SectionPolicy,
    before: str,
    after: str,
    *,
    used_fact_ids: list[str] | None = None,
) -> list[PolicyViolation]:
    violations: list[PolicyViolation] = []
    rendered = after.strip()
    if policy.required and not rendered:
        violations.append(
            PolicyViolation(code="required_content_missing", message="Content is required.")
        )
    if policy.fidelity is FidelityLevel.LOCKED and after != before:
        violations.append(
            PolicyViolation(code="locked_content_changed", message="Locked content changed.")
        )
    normalized_after = " ".join(rendered.casefold().split())
    for term in policy.protected_terms:
        if " ".join(term.casefold().split()) not in normalized_after:
            violations.append(
                PolicyViolation(
                    code="protected_term_missing",
                    message=f"Protected term is missing: {term}",
                )
            )
    if policy.min_characters is not None and len(rendered) < policy.min_characters:
        violations.append(
            PolicyViolation(
                code="below_min_characters",
                message=f"Content has {len(rendered)} characters; minimum is {policy.min_characters}.",
            )
        )
    if policy.max_characters is not None and len(rendered) > policy.max_characters:
        violations.append(
            PolicyViolation(
                code="above_max_characters",
                message=f"Content has {len(rendered)} characters; maximum is {policy.max_characters}.",
            )
        )
    used = set(used_fact_ids or [])
    for fact_id in policy.protected_fact_ids:
        if fact_id not in used:
            violations.append(
                PolicyViolation(
                    code="protected_fact_missing",
                    message=f"Protected fact is not referenced: {fact_id}",
                )
            )
    return violations
