from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SubmissionMode(StrEnum):
    DRY_RUN = "dry_run"
    CONFIRM = "confirm"
    AUTOMATIC = "automatic"


class InterventionAction(StrEnum):
    PAUSE = "pause"
    REQUEST_USER = "request_user"


class SubmissionPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    mode: SubmissionMode = SubmissionMode.CONFIRM
    max_applications_per_campaign: int = Field(default=5, ge=1, le=100)
    allowed_portals: list[str] = Field(default_factory=list)
    standard_answers: dict[str, str] = Field(default_factory=dict)
    allowed_consents: list[str] = Field(default_factory=list)
    max_retries: int = Field(default=1, ge=0, le=5)
    on_login: InterventionAction = InterventionAction.REQUEST_USER
    on_captcha: InterventionAction = InterventionAction.REQUEST_USER
    on_two_factor: InterventionAction = InterventionAction.REQUEST_USER
    on_ambiguous_field: InterventionAction = InterventionAction.REQUEST_USER
    require_confirmation_evidence: bool = True

    @field_validator("allowed_portals", "allowed_consents")
    @classmethod
    def clean_terms(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            compact = " ".join(value.split())
            if not compact:
                continue
            key = compact.casefold()
            if key not in seen:
                cleaned.append(compact)
                seen.add(key)
        return cleaned

    @field_validator("standard_answers")
    @classmethod
    def clean_standard_answers(cls, values: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for key, value in values.items():
            compact_key = " ".join(key.split())
            compact_value = " ".join(value.split())
            if not compact_key or not compact_value:
                continue
            if len(compact_key) > 200 or len(compact_value) > 2_000:
                raise ValueError("standard answer keys or values are too long")
            cleaned[compact_key] = compact_value
        return cleaned

    @classmethod
    def load(cls, path: Path) -> SubmissionPreferences:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
