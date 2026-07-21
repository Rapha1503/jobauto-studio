from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from jobauto.project_lab_policy import ProjectLabPolicy
from jobauto.text_encoding import repair_utf8_mojibake

PROFILE_PATH_FIELDS = (
    "facts_path",
    "project_bank_path",
    "skill_policy_path",
    "cv_model_path",
    "cv_mapping_path",
    "cv_source_path",
    "letter_model_path",
    "adaptation_policy_path",
    "role_profiles_path",
    "search_preferences_path",
    "submission_preferences_path",
    "form_profile_path",
)


class CvBackend(StrEnum):
    GENERATED_TEMPLATE = "markdown_generated"
    SOURCE_PRESERVING = "latex_source_preserving"


class CandidateIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    phone: str | None = Field(default=None, max_length=80)
    location: str | None = Field(default=None, max_length=160)

    @field_validator("first_name", "last_name", "email", "phone", "location")
    @classmethod
    def compact_identity_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        compact = " ".join(repair_utf8_mojibake(value).split())
        return compact or None


class CandidateProfile(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    schema_version: int = Field(default=1, ge=1)
    candidate_id: str = Field(min_length=2, max_length=80)
    identity: CandidateIdentity
    locale: str = Field(default="fr-FR", min_length=2, max_length=20)
    availability: str | None = Field(default=None, max_length=160)
    work_authorization: str | None = Field(default=None, max_length=160)
    facts_path: Path
    project_bank_path: Path
    skill_policy_path: Path
    cv_backend: CvBackend = CvBackend.GENERATED_TEMPLATE
    cv_model_path: Path
    cv_mapping_path: Path | None = None
    cv_source_path: Path | None = None
    letter_model_path: Path
    adaptation_policy_path: Path | None = None
    role_profiles_path: Path | None = None
    search_preferences_path: Path | None = None
    submission_preferences_path: Path | None = None
    form_profile_path: Path | None = None
    protected_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    project_lab: ProjectLabPolicy = Field(default_factory=ProjectLabPolicy)

    _source_path: Path | None = PrivateAttr(default=None)

    @field_validator("candidate_id")
    @classmethod
    def candidate_id_is_portable(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,79}", normalized):
            raise ValueError("candidate_id must use lowercase letters, numbers, '-' or '_'")
        return normalized

    @model_validator(mode="after")
    def source_preserving_profile_has_mapping(self) -> CandidateProfile:
        if self.cv_backend is CvBackend.SOURCE_PRESERVING and self.cv_mapping_path is None:
            raise ValueError("source-preserving CV profile requires cv_mapping_path")
        return self

    @classmethod
    def load(cls, path: Path) -> CandidateProfile:
        source_path = path.expanduser().resolve()
        data = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
        resolved = _resolve_profile_paths(data, source_path.parent)
        profile = cls.model_validate(resolved)
        profile._source_path = source_path
        profile.validate_assets()
        return profile

    def validate_assets(self) -> None:
        missing = [
            name
            for name in PROFILE_PATH_FIELDS
            if (path := getattr(self, name)) is not None and not path.is_file()
        ]
        if missing:
            raise ValueError(f"Missing candidate profile assets: {', '.join(missing)}")

    @property
    def asset_paths(self) -> dict[str, Path]:
        return {
            "facts": self.facts_path,
            "project_bank": self.project_bank_path,
            "skill_policy": self.skill_policy_path,
            "cv_model": self.cv_model_path,
            "letter_model": self.letter_model_path,
            **({"cv_source": self.cv_source_path} if self.cv_source_path is not None else {}),
            **(
                {"adaptation_policy": self.adaptation_policy_path}
                if self.adaptation_policy_path is not None
                else {}
            ),
            **(
                {"role_profiles": self.role_profiles_path}
                if self.role_profiles_path is not None
                else {}
            ),
            **(
                {"search_preferences": self.search_preferences_path}
                if self.search_preferences_path is not None
                else {}
            ),
            **(
                {"submission_preferences": self.submission_preferences_path}
                if self.submission_preferences_path is not None
                else {}
            ),
            **(
                {"form_profile": self.form_profile_path}
                if self.form_profile_path is not None
                else {}
            ),
        }

    @property
    def source_path(self) -> Path | None:
        return self._source_path

    @property
    def profile_hash(self) -> str:
        profile_data = self.model_dump(mode="json", exclude=set(PROFILE_PATH_FIELDS))
        assets = {
            name: _file_sha256(path)
            for name in PROFILE_PATH_FIELDS
            if (path := getattr(self, name)) is not None
        }
        payload = json.dumps(
            {"profile": profile_data, "assets": assets},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def legacy_candidate_profile(
    *,
    candidate_id: str,
    identity_path: Path,
    facts_path: Path,
    project_bank_path: Path,
    skill_policy_path: Path,
    cv_model_path: Path,
    letter_model_path: Path,
    role_profiles_path: Path | None = None,
    locale: str = "fr-FR",
) -> CandidateProfile:
    identity_data = yaml.safe_load(identity_path.read_text(encoding="utf-8")) or {}
    profile = CandidateProfile(
        candidate_id=candidate_id,
        identity=CandidateIdentity.model_validate(identity_data),
        locale=locale,
        availability=identity_data.get("availability"),
        work_authorization=identity_data.get("work_authorization"),
        facts_path=facts_path.resolve(),
        project_bank_path=project_bank_path.resolve(),
        skill_policy_path=skill_policy_path.resolve(),
        cv_model_path=cv_model_path.resolve(),
        letter_model_path=letter_model_path.resolve(),
        role_profiles_path=role_profiles_path.resolve() if role_profiles_path else None,
    )
    profile._source_path = identity_path.resolve()
    profile.validate_assets()
    return profile


def _resolve_profile_paths(data: dict[str, Any], root: Path) -> dict[str, Any]:
    resolved = dict(data)
    for name in PROFILE_PATH_FIELDS:
        value = resolved.get(name)
        if value in {None, ""}:
            continue
        path = Path(str(value)).expanduser()
        resolved[name] = (path if path.is_absolute() else root / path).resolve()
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
