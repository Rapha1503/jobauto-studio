from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_VISIBLE_PROJECTS = 3


class ProjectLabPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_new_project: bool = False
    allow_external_inspiration: bool = False
    require_verification_warning: bool = True
    minimum_visible_projects: int = Field(default=0, ge=0, le=MAX_VISIBLE_PROJECTS)
    maximum_visible_projects: int = Field(default=0, ge=0, le=MAX_VISIBLE_PROJECTS)

    @model_validator(mode="after")
    def project_count_range_is_valid(self) -> ProjectLabPolicy:
        if self.minimum_visible_projects > self.maximum_visible_projects:
            raise ValueError("minimum_visible_projects cannot exceed maximum_visible_projects")
        return self
