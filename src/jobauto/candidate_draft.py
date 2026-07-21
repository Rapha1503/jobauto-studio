from __future__ import annotations

import json
import os
import tempfile
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobauto.adaptation_policy import CvLayoutPolicy, FidelityLevel
from jobauto.latex_cv_source import LatexCvMapping
from jobauto.profile_extraction import CandidateProfileExtraction
from jobauto.project_lab_policy import ProjectLabPolicy
from jobauto.search_preferences import SearchPreferences
from jobauto.submission_preferences import SubmissionPreferences


class DraftStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    VALIDATED = "validated"


class DraftOrigin(StrEnum):
    LATEX = "latex"
    PDF = "pdf"
    MANUAL = "manual"


class DraftJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SkillUsage(StrEnum):
    REQUIRED = "required"
    DEFAULT = "default"
    REMOVABLE = "removable"


class SkillEvidence(StrEnum):
    VERIFIED = "verified"
    TRANSFERABLE = "transferable"
    FORBIDDEN = "forbidden"


class ProjectUseMode(StrEnum):
    REUSE = "reuse"
    REFRAME = "reframe"
    DERIVE = "derive"


class DraftIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    headline: str | None = None
    source_block_ids: list[str] = Field(default_factory=list)

    @field_validator("first_name", "last_name", "email", "phone", "location", "headline")
    @classmethod
    def compact_text(cls, value: str | None) -> str | None:
        compact = " ".join(value.split()) if value else ""
        return compact or None


class DraftExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experience_id: str
    organization: str
    role: str
    location: str | None = None
    dates: str | None = None
    sector: str | None = None
    tools: list[str] = Field(default_factory=list)
    facts: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    protected_fields: list[str] = Field(default_factory=lambda: ["organization", "role", "dates"])
    allowed_angles: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)


class DraftSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    category: str
    usage: SkillUsage = SkillUsage.DEFAULT
    evidence: SkillEvidence = SkillEvidence.VERIFIED
    verification_warning: bool = False
    source_block_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def forbidden_skill_is_never_required(self) -> DraftSkill:
        if self.evidence is SkillEvidence.FORBIDDEN and self.usage is SkillUsage.REQUIRED:
            raise ValueError("a forbidden skill cannot be required")
        return self


class DraftProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    title: str
    stack: list[str] = Field(default_factory=list)
    description: list[str] = Field(default_factory=list)
    visible_by_default: bool = True
    cv_eligible: bool = True
    use_mode: ProjectUseMode = ProjectUseMode.REFRAME
    title_fidelity: FidelityLevel = FidelityLevel.VERY_FAITHFUL
    stack_fidelity: FidelityLevel = FidelityLevel.ADAPTABLE
    description_fidelity: FidelityLevel = FidelityLevel.ADAPTABLE
    source_block_ids: list[str] = Field(default_factory=list)


class DraftAdditionalSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    fidelity: FidelityLevel = FidelityLevel.VERY_FAITHFUL
    source_block_ids: list[str] = Field(default_factory=list)


class CandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    origin: DraftOrigin = DraftOrigin.LATEX
    draft_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    version: int = Field(default=1, ge=1)
    status: DraftStatus = DraftStatus.NEEDS_REVIEW
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapping_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_document_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    source_document_filename: str | None = Field(default=None, max_length=300)
    source_document_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    locale: str = Field(default="fr-FR", min_length=2, max_length=20)
    identity: DraftIdentity
    summary: str | None = None
    summary_source_block_ids: list[str] = Field(default_factory=list)
    letter_reference: str | None = Field(default=None, max_length=20_000)
    experiences: list[DraftExperience] = Field(default_factory=list)
    skills: list[DraftSkill] = Field(default_factory=list)
    projects: list[DraftProject] = Field(default_factory=list)
    education: list[dict[str, object]] = Field(default_factory=list)
    additional_sections: list[DraftAdditionalSection] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    cv_layout: CvLayoutPolicy = Field(default_factory=CvLayoutPolicy)
    project_lab: ProjectLabPolicy = Field(default_factory=ProjectLabPolicy)
    search_preferences: SearchPreferences = Field(default_factory=SearchPreferences)
    submission_preferences: SubmissionPreferences = Field(default_factory=SubmissionPreferences)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_extraction(
        cls,
        *,
        import_id: str,
        mapping: LatexCvMapping,
        extraction: CandidateProfileExtraction,
        origin: DraftOrigin = DraftOrigin.LATEX,
        source_document_id: str | None = None,
        source_document_filename: str | None = None,
        source_document_sha256: str | None = None,
    ) -> CandidateDraft:
        projects = [DraftProject(**item.model_dump(mode="python")) for item in extraction.projects]
        return cls(
            origin=origin,
            draft_id=uuid4().hex,
            import_id=import_id,
            source_sha256=mapping.source_sha256,
            mapping_hash=mapping.mapping_hash,
            source_document_id=source_document_id,
            source_document_filename=source_document_filename,
            source_document_sha256=source_document_sha256,
            locale=extraction.locale or "fr-FR",
            identity=DraftIdentity(**extraction.identity.model_dump(mode="python")),
            summary=extraction.summary,
            summary_source_block_ids=extraction.summary_source_block_ids,
            experiences=[
                DraftExperience(**item.model_dump(mode="python")) for item in extraction.experiences
            ],
            skills=[DraftSkill(**item.model_dump(mode="python")) for item in extraction.skills],
            projects=projects,
            project_lab=ProjectLabPolicy(
                minimum_visible_projects=min(3, len(projects)),
                maximum_visible_projects=3 if projects else 0,
            ),
            education=[item.model_dump(mode="json") for item in extraction.education],
            additional_sections=[
                DraftAdditionalSection(**item.model_dump(mode="python"))
                for item in extraction.additional_sections
            ],
            languages=extraction.languages,
            interests=extraction.interests,
            warnings=extraction.warnings,
        )

    @classmethod
    def manual(
        cls,
        *,
        import_id: str,
        mapping: LatexCvMapping,
        locale: str = "en-GB",
    ) -> CandidateDraft:
        return cls(
            origin=DraftOrigin.MANUAL,
            draft_id=uuid4().hex,
            import_id=import_id,
            source_sha256=mapping.source_sha256,
            mapping_hash=mapping.mapping_hash,
            locale=locale,
            identity=DraftIdentity(),
            project_lab=ProjectLabPolicy(
                minimum_visible_projects=0,
                maximum_visible_projects=3,
            ),
        )


class CandidateDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    locale: str = Field(default="fr-FR", min_length=2, max_length=20)
    identity: DraftIdentity
    summary: str | None = None
    summary_source_block_ids: list[str] = Field(default_factory=list)
    letter_reference: str | None = Field(default=None, max_length=20_000)
    experiences: list[DraftExperience] = Field(default_factory=list)
    skills: list[DraftSkill] = Field(default_factory=list)
    projects: list[DraftProject] = Field(default_factory=list)
    education: list[dict[str, object]] = Field(default_factory=list)
    additional_sections: list[DraftAdditionalSection] | None = None
    languages: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    cv_layout: CvLayoutPolicy = Field(default_factory=CvLayoutPolicy)
    project_lab: ProjectLabPolicy = Field(default_factory=ProjectLabPolicy)
    search_preferences: SearchPreferences = Field(default_factory=SearchPreferences)
    submission_preferences: SubmissionPreferences = Field(default_factory=SubmissionPreferences)


class CandidateDraftValidation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[str] = Field(default_factory=list)


def update_candidate_draft(draft: CandidateDraft, update: CandidateDraftUpdate) -> CandidateDraft:
    return draft.model_copy(
        update={
            "status": DraftStatus.NEEDS_REVIEW,
            "locale": update.locale,
            "identity": update.identity,
            "summary": update.summary,
            "summary_source_block_ids": update.summary_source_block_ids,
            "letter_reference": update.letter_reference,
            "experiences": update.experiences,
            "skills": update.skills,
            "projects": update.projects,
            "education": update.education,
            "additional_sections": (
                draft.additional_sections
                if update.additional_sections is None
                else update.additional_sections
            ),
            "languages": update.languages,
            "interests": update.interests,
            "cv_layout": update.cv_layout,
            "project_lab": update.project_lab,
            "search_preferences": update.search_preferences,
            "submission_preferences": update.submission_preferences,
        }
    )


def validate_candidate_draft(
    draft: CandidateDraft, mapping: LatexCvMapping
) -> CandidateDraftValidation:
    errors: list[str] = []
    if draft.source_sha256 != mapping.source_sha256:
        errors.append("The imported CV changed after profile extraction.")
    if draft.mapping_hash != mapping.mapping_hash:
        errors.append("The confirmed CV mapping changed after profile extraction.")

    for label, value in (
        ("first name", draft.identity.first_name),
        ("last name", draft.identity.last_name),
        ("email", draft.identity.email),
    ):
        if not value:
            errors.append(f"Candidate {label} is required.")
    evidence_sections = (
        draft.experiences,
        draft.projects,
        draft.education,
        draft.additional_sections,
    )
    if not any(evidence_sections):
        errors.append(
            "At least one experience, project, education entry or additional evidence section "
            "is required."
        )
    if draft.origin is DraftOrigin.LATEX:
        allowed = {block.block_id for block in mapping.blocks}
        referenced = [draft.identity.source_block_ids]
        referenced.append(draft.summary_source_block_ids)
        referenced.extend(item.source_block_ids for item in draft.experiences)
        referenced.extend(item.source_block_ids for item in draft.skills)
        referenced.extend(item.source_block_ids for item in draft.projects)
        referenced.extend(
            list(item.get("source_block_ids", []))
            for item in draft.education
            if isinstance(item, dict)
        )
        referenced.extend(item.source_block_ids for item in draft.additional_sections)
        unknown = sorted(
            {
                block_id
                for source_ids in referenced
                for block_id in source_ids
                if block_id not in allowed
            }
        )
        if unknown:
            errors.append(f"Profile items cite unknown CV blocks: {', '.join(unknown)}")

    for label, identifiers in (
        ("experience", [item.experience_id for item in draft.experiences]),
        ("project", [item.project_id for item in draft.projects]),
    ):
        duplicates = sorted(
            {identifier for identifier in identifiers if identifiers.count(identifier) > 1}
        )
        if duplicates:
            errors.append(f"Duplicate {label} ids: {', '.join(duplicates)}")
    return CandidateDraftValidation(valid=not errors, errors=errors)


class CandidateDraftJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: DraftJobStatus
    draft_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    error: str | None = None


class CandidateDraftStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, draft: CandidateDraft) -> CandidateDraft:
        path = self._path(draft.draft_id)
        if path.exists():
            raise ValueError(f"candidate draft already exists: {draft.draft_id}")
        self._write(path, draft)
        return draft

    def get(self, draft_id: str) -> CandidateDraft:
        path = self._path(draft_id)
        if not path.is_file():
            raise FileNotFoundError(draft_id)
        return CandidateDraft.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, draft: CandidateDraft, *, expected_version: int) -> CandidateDraft:
        current = self.get(draft.draft_id)
        if current.version != expected_version:
            raise ValueError(
                f"candidate draft version conflict: expected {expected_version}, "
                f"found {current.version}"
            )
        updated = draft.model_copy(update={"version": current.version + 1})
        self._write(self._path(draft.draft_id), updated)
        return updated

    def start_job(self, import_id: str) -> CandidateDraftJob:
        job = CandidateDraftJob(import_id=import_id, status=DraftJobStatus.PENDING)
        self._write_model(self._job_path(import_id), job)
        return job

    def get_job(self, import_id: str) -> CandidateDraftJob:
        path = self._job_path(import_id)
        if not path.is_file():
            raise FileNotFoundError(import_id)
        return CandidateDraftJob.model_validate_json(path.read_text(encoding="utf-8"))

    def update_job(self, job: CandidateDraftJob) -> CandidateDraftJob:
        self._write_model(self._job_path(job.import_id), job)
        return job

    def _path(self, draft_id: str) -> Path:
        if not draft_id or any(character not in "0123456789abcdef" for character in draft_id):
            raise FileNotFoundError(draft_id)
        path = (self.root / f"{draft_id}.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(draft_id) from exc
        return path

    def _job_path(self, import_id: str) -> Path:
        if not import_id or any(character not in "0123456789abcdef" for character in import_id):
            raise FileNotFoundError(import_id)
        path = (self.root / "jobs" / f"{import_id}.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(import_id) from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _write(path: Path, draft: CandidateDraft) -> None:
        CandidateDraftStore._write_model(path, draft)

    @staticmethod
    def _write_model(path: Path, model: BaseModel) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(model.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            Path(temporary_name).unlink(missing_ok=True)
            raise
