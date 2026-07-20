from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jobauto.cv_source import CvSourceDocument


class CandidateFormExperience(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    organization: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=240)
    location: str | None = Field(default=None, max_length=160)
    dates: str | None = Field(default=None, max_length=120)
    description: list[str] = Field(default_factory=list)


class CandidateFormEducation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    institution: str = Field(min_length=1, max_length=200)
    program: str = Field(min_length=1, max_length=240)
    location: str | None = Field(default=None, max_length=160)
    dates: str | None = Field(default=None, max_length=120)
    details: list[str] = Field(default_factory=list)


class CandidateFormProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    experiences: list[CandidateFormExperience] = Field(default_factory=list)
    education: list[CandidateFormEducation] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> CandidateFormProfile:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def from_cv_source(cls, source: CvSourceDocument) -> CandidateFormProfile:
        return cls(
            experiences=[
                CandidateFormExperience(
                    organization=_split_title(entry.title)[0],
                    role=_split_title(entry.title)[1],
                    dates=entry.dates,
                    description=entry.bullets,
                )
                for entry in source.experience
            ],
            education=[
                CandidateFormEducation(
                    institution=_split_title(entry.title)[0],
                    program=_split_title(entry.title)[1],
                    dates=entry.dates,
                    details=entry.bullets,
                )
                for entry in source.education
            ],
            languages=[part.strip() for part in source.languages.split("|") if part.strip()],
        )


def _split_title(value: str) -> tuple[str, str]:
    for separator in (" – ", " — ", " - "):
        if separator in value:
            organization, role = value.split(separator, 1)
            if organization.strip() and role.strip():
                return organization.strip(), role.strip()
    return value.strip(), value.strip()
