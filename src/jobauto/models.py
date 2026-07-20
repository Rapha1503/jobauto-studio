from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobauto.project_lab_policy import MAX_VISIBLE_PROJECTS

if TYPE_CHECKING:
    from jobauto.candidate_snapshot import CandidateSnapshot


# Advisory prompt budget only. Candidate-specific CV policy and PDF rendering
# remain authoritative for the final visual layout.
SKILL_LINE_MAX_CHARS = 118


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_value)).strip("-")


class FactStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICT = "conflict"


class RoleFamily(StrEnum):
    DEFAULT = "Default"
    AI_ENGINEER = "AI Engineer"
    MACHINE_LEARNING_ENGINEER = "Machine Learning Engineer"
    DATA_SCIENTIST = "Data Scientist"
    DATA_ENGINEER = "Data Engineer"
    ANALYTICS_ENGINEER = "Analytics Engineer"
    DATA_ANALYST = "Data Analyst"
    CONSULTANT_DATA = "Consultant Data"
    CONSULTANT_IA = "Consultant IA"
    CONSULTANT_TRANSFORMATION = "Consultant Transformation"
    PRODUCT_MANAGER = "Product Manager"
    PRODUCT_OWNER = "Product Owner"
    CLOUD_ENGINEER = "Cloud Engineer"
    PROCESS_INTELLIGENCE_ENGINEER = "Process Intelligence Engineer"
    PROCESS_MINING_ENGINEER = "Process Mining Engineer"


class SkillGroupName(StrEnum):
    GEN_AI = "GenAI"
    MACHINE_LEARNING = "Machine Learning"
    DATA_ENGINEERING = "Data Engineering"
    DEVELOPPEMENT_IA = "Développement IA"


class LetterFocus(StrEnum):
    GENAI_APPLICATIONS = "genai_applications"
    MODELING_AND_EXPERIMENTATION = "modeling_and_experimentation"
    DATA_PLATFORMS = "data_platforms"
    BUSINESS_IMPACT = "business_impact"
    TRANSFORMATION = "transformation"
    CLOUD_INFRASTRUCTURE = "cloud_infrastructure"
    PROCESS_INTELLIGENCE = "process_intelligence"
    PRODUCT_VISION = "product_vision"


class CandidateFact(BaseModel):
    fact_id: str
    claim: str
    status: FactStatus
    role_tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    def require_approved(self) -> CandidateFact:
        if self.status is not FactStatus.VERIFIED:
            raise ValueError(f"Fact {self.fact_id} is not approved")
        return self


class ApplicationRow(BaseModel):
    excel_row: int
    company: str
    role: str
    url: str
    description: str | None = None

    def output_directory(self, root: Path) -> Path:
        name = f"L{self.excel_row:03d}_{slugify(self.company)}_{slugify(self.role)}"
        return root / name


class JobProfile(BaseModel):
    company: str
    role: str
    role_family: str
    language: str = "fr"
    summary: str
    responsibilities: list[str]
    required_skills: list[str]
    preferred_skills: list[str] = Field(default_factory=list)
    company_details: list[str] = Field(default_factory=list)
    seniority: str = "unspecified"


class OfferAnalysis(JobProfile):
    """Agentic reading of the offer, with enough context to guide writers."""

    normalized_role: str = "Target role"
    targeted_keywords: list[str] = Field(default_factory=list)
    cv_angle: str = "Candidature factuelle centree sur les exigences du poste."
    letter_angle: str = "Lettre concrete centree sur les missions et l'environnement de travail."
    adaptation_guidance: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def replace_schema_placeholders_with_the_observed_role(self) -> OfferAnalysis:
        if self.normalized_role.strip().casefold() in {"default", "target role"}:
            self.normalized_role = self.role
        if str(self.role_family).strip().casefold() == RoleFamily.DEFAULT.value.casefold():
            self.role_family = self.normalized_role
        return self


class ProjectDraft(BaseModel):
    key: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=3, max_length=120)
    stack: str = Field(min_length=3, max_length=180)
    bullet: str = Field(min_length=20, max_length=420)

    @field_validator("key", "title", "stack", "bullet")
    @classmethod
    def project_fields_are_single_line(cls, value: str) -> str:
        if "\n" in value:
            raise ValueError("project fields must stay on one line")
        return value.strip()


class CvDraft(BaseModel):
    headline: str = Field(min_length=10, max_length=120, pattern=r"^[^\r\n]+$")
    summary: str = Field(min_length=10, max_length=650)
    experience_bullets: list[str] = Field(min_length=3, max_length=5)
    internal_project_bullets: list[str] = Field(min_length=2, max_length=3)
    selected_projects: list[str] = Field(min_length=2, max_length=4)
    project_entries: list[ProjectDraft] = Field(default_factory=list, max_length=4)
    skill_groups: dict[SkillGroupName, list[str]] = Field(min_length=3, max_length=4)
    skill_sections: dict[str, list[str]] = Field(default_factory=dict, max_length=5)
    used_fact_ids: list[str]
    adaptation_notes: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)

    @field_validator("experience_bullets", "internal_project_bullets")
    @classmethod
    def bullets_are_single_paragraphs(cls, bullets: list[str]) -> list[str]:
        for bullet in bullets:
            if "\n" in bullet or len(bullet) > 340:
                raise ValueError(
                    "bullets must be concise single paragraphs (<=340 chars, no newlines)"
                )
        return bullets

    @field_validator("selected_projects")
    @classmethod
    def selected_project_ids_are_safe(cls, projects: list[str]) -> list[str]:
        pattern = re.compile(r"^[a-z0-9_]{2,80}$")
        normalized = []
        for project in projects:
            value = str(project).strip()
            if not pattern.match(value):
                raise ValueError(f"unsafe project id: {project!r}")
            normalized.append(value)
        return normalized

    @field_validator("skill_groups")
    @classmethod
    def skills_per_group(
        cls, groups: dict[SkillGroupName, list[str]]
    ) -> dict[SkillGroupName, list[str]]:
        for name, skills in groups.items():
            if not 3 <= len(skills) <= 24:
                raise ValueError(f"skill group {name} must contain 3-24 entries")
            for skill in skills:
                if "\n" in skill or len(skill) > 60:
                    raise ValueError("skill entries must be single-line <= 60 chars")
        return groups

    @field_validator("skill_sections")
    @classmethod
    def skill_sections_are_compact(cls, groups: dict[str, list[str]]) -> dict[str, list[str]]:
        for name, skills in groups.items():
            if "\n" in name or not 2 <= len(name) <= 40:
                raise ValueError("skill section names must be compact single-line labels")
            if not 3 <= len(skills) <= 24:
                raise ValueError(f"skill section {name} must contain 3-24 entries")
            for skill in skills:
                if "\n" in skill or len(skill) > 60:
                    raise ValueError("skill entries must be single-line <= 60 chars")
        return groups


class LetterDraft(BaseModel):
    greeting: str
    paragraphs: list[str] = Field(min_length=1)
    closing: str
    used_fact_ids: list[str]
    adaptation_notes: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)

    @field_validator("paragraphs")
    @classmethod
    def paragraphs_are_single(cls, paragraphs: list[str]) -> list[str]:
        for paragraph in paragraphs:
            if not paragraph.strip() or "\n\n" in paragraph:
                raise ValueError("paragraphs must be non-blank single blocks")
        return paragraphs


class CandidateLetterDraft(LetterDraft):
    model_config = ConfigDict(extra="forbid")

    def validate_for_snapshot(self, snapshot: CandidateSnapshot) -> CandidateLetterDraft:
        identity = snapshot.profile.identity
        expected_signature = slugify(f"{identity.first_name} {identity.last_name}")
        if expected_signature not in slugify(self.closing):
            raise ValueError("letter signature does not match candidate identity")
        used_fact_ids = list(dict.fromkeys(self.used_fact_ids))
        snapshot.require_evidence_ids(used_fact_ids)
        snapshot.require_protected_claim_values(
            "\n".join([self.greeting, *self.paragraphs, self.closing]),
            used_fact_ids,
        )
        return self


def validate_candidate_letter_claim_values(
    snapshot: CandidateSnapshot,
    letter: CandidateLetterDraft,
    offer_text: str,
) -> None:
    snapshot.require_supported_claim_values(
        "\n".join([letter.greeting, *letter.paragraphs, letter.closing]),
        list(dict.fromkeys(letter.used_fact_ids)),
        additional_evidence=[offer_text],
    )


class RepairAction(BaseModel):
    surface: Literal["cv", "letter", "cross_document"]
    field: str = Field(min_length=1, max_length=80)
    problem: str = Field(min_length=5, max_length=400)
    instruction: str = Field(min_length=5, max_length=500)
    must_preserve: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("field", "problem", "instruction")
    @classmethod
    def repair_text_is_single_line(cls, value: str) -> str:
        compact = " ".join(value.split())
        if not compact:
            raise ValueError("repair action text must not be blank")
        return compact


class RenderedRequirementCoverage(BaseModel):
    requirement_id: str = Field(min_length=1, max_length=80)
    coverage: Literal["exact", "semantic", "indirect", "missing"]
    placements: list[str] = Field(default_factory=list, max_length=12)
    supporting_excerpts: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(min_length=5, max_length=500)

    @field_validator("requirement_id", "rationale")
    @classmethod
    def coverage_text_is_non_blank(cls, value: str) -> str:
        compact = " ".join(value.split())
        if not compact:
            raise ValueError("coverage fields must not be blank")
        return compact


class AtsScoreBreakdown(BaseModel):
    """Explainable JobAuto estimate, not a vendor-specific ATS score."""

    model_config = ConfigDict(extra="forbid", strict=True)

    model: Literal["jobauto_ats_readiness_v1"] = "jobauto_ats_readiness_v1"
    score: int = Field(ge=0, le=100)
    weighted_requirement_score: int = Field(ge=0, le=100)
    exact_term_score: int | None = Field(default=None, ge=0, le=100)
    priority_scores: dict[Literal["must", "important", "nice"], int]
    critical_requirement_ids: list[str] = Field(default_factory=list)
    weak_central_requirement_ids: list[str] = Field(default_factory=list)
    parseable: bool
    role_positioning_matches: bool
    language_matches: bool
    ready_without_cv_changes: bool
    adaptation_recommended: bool
    decision_reasons: list[str] = Field(default_factory=list)


class LetterEditorialReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    score: int = Field(ge=0, le=100)
    blocking_issues: list[str]
    improvements: list[str]
    repair_actions: list[RepairAction]

    @model_validator(mode="after")
    def editorial_state_is_consistent(self) -> LetterEditorialReview:
        if any(action.surface != "letter" for action in self.repair_actions):
            raise ValueError("letter editorial review actions must target the letter")
        if self.approved:
            if self.score < 90:
                raise ValueError("approved review requires a score of at least 90")
            if self.blocking_issues or self.repair_actions:
                raise ValueError("approved letter editorial review cannot contain blockers")
            return self
        if (
            not any(issue.strip() for issue in self.blocking_issues)
            or not any(improvement.strip() for improvement in self.improvements)
            or not self.repair_actions
        ):
            raise ValueError(
                "rejected letter editorial review requires an issue, improvement, and repair action"
            )
        return self


class AgenticLetterDraft(LetterDraft):
    @field_validator("greeting")
    @classmethod
    def greeting_is_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("agentic letter greeting must not be blank")
        return value.strip()

    @field_validator("closing")
    @classmethod
    def closing_is_signed(cls, value: str) -> str:
        lines = [line.strip() for line in value.splitlines() if line.strip()]
        if len(lines) < 2 or len(lines[-1].split()) < 2:
            raise ValueError("agentic letter closing must end with a full candidate signature")
        return "\n".join(lines)


class ReviewResult(BaseModel):
    approved: bool
    score: int = Field(ge=0, le=100)
    blocking_issues: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    adaptation_score: int = Field(default=0, ge=0, le=100)
    repaired_cv: CvDraft | None = None
    repaired_letter: LetterDraft | None = None


BlockingCategory = Literal[
    "role_or_scope",
    "sector_or_context",
    "requirement_coverage",
    "ats_or_skills",
    "evidence_or_credibility",
    "primary_experience",
    "projects",
    "cv_letter_coherence",
    "writing_quality",
    "self_sabotage",
    "technical_form",
    "other",
]


class ReviewQualityChecks(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary_fidelity: Literal["pass", "repair", "not_assessed"]
    project_strategy: Literal["pass", "repair", "not_assessed"]
    skill_strategy: Literal["pass", "repair", "not_assessed"]
    letter_argument: Literal["pass", "repair", "not_assessed"]


class RenderedApplicationReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    score: int = Field(ge=0, le=100)
    ats_score: int = Field(ge=0, le=100)
    editorial_score: int = Field(ge=0, le=100)
    reference_fidelity_score: int = Field(ge=0, le=100)
    adaptation_score: int = Field(ge=0, le=100)
    quality_checks: ReviewQualityChecks
    blocking_categories: list[BlockingCategory]
    blocking_issues: list[str]
    improvements: list[str]
    warnings: list[str]
    repair_actions: list[RepairAction]
    requirement_coverage: list[RenderedRequirementCoverage]

    @model_validator(mode="after")
    def review_state_is_consistent(self) -> RenderedApplicationReview:
        if self.approved:
            if self.score < 90:
                raise ValueError("approved review requires a score of at least 90")
            if any(value != "pass" for value in self.quality_checks.model_dump().values()):
                raise ValueError("approved review requires every semantic quality check to pass")
            if self.blocking_categories or self.blocking_issues:
                raise ValueError("approved review cannot contain blocking categories or issues")
            if self.repair_actions:
                raise ValueError("approved review cannot contain repair actions")
            return self
        if (
            not self.blocking_categories
            or not any(issue.strip() for issue in self.blocking_issues)
            or not any(improvement.strip() for improvement in self.improvements)
        ):
            raise ValueError(
                "rejected review requires a blocking category, substantive issue, and improvement"
            )
        return self


class CandidateRepairAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    surface: Literal["cv", "letter", "both"]
    instruction: str = Field(min_length=10, max_length=800)


LetterArgumentState = Literal["pass", "repair", "not_assessed"]


class LetterArgumentCriterionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    state: LetterArgumentState
    rationale: str = Field(min_length=20, max_length=600)
    supporting_excerpt: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def passed_criterion_has_visible_support(self):
        if self.state == "pass" and not (self.supporting_excerpt or "").strip():
            raise ValueError("a passed letter criterion requires a supporting excerpt")
        return self


class LetterArgumentAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    target_specificity: LetterArgumentCriterionAssessment = Field(
        description=(
            "Whether the letter gives a sourced, role-specific reason for this application "
            "instead of a generic application statement or flattery."
        )
    )
    evidence_to_missions: LetterArgumentCriterionAssessment = Field(
        description=(
            "Whether a small selection of verified evidence is connected to the offer's central "
            "missions rather than listed without an argument."
        )
    )
    candidate_contribution: LetterArgumentCriterionAssessment = Field(
        description=(
            "Whether the letter explains what the candidate can contribute in the target context."
        )
    )
    motivation_credibility: LetterArgumentCriterionAssessment = Field(
        description=(
            "Whether the letter explains a credible candidate-specific interest beyond generic fit."
        )
    )
    tone_and_naturalness: LetterArgumentCriterionAssessment = Field(
        description=(
            "Whether the writing is natural, concise and professional rather than boilerplate."
        )
    )

    @property
    def criteria(self) -> tuple[LetterArgumentCriterionAssessment, ...]:
        return (
            self.target_specificity,
            self.evidence_to_missions,
            self.candidate_contribution,
            self.motivation_credibility,
            self.tone_and_naturalness,
        )

    @property
    def states(self) -> tuple[LetterArgumentState, ...]:
        return tuple(criterion.state for criterion in self.criteria)


class CandidateApplicationReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    score: int = Field(ge=0, le=100)
    ats_score: int = Field(ge=0, le=100)
    ats_breakdown: AtsScoreBreakdown | None = None
    editorial_score: int = Field(ge=0, le=100)
    adaptation_score: int = Field(ge=0, le=100)
    blocking_issues: list[str]
    warnings: list[str]
    letter_argument: LetterArgumentAssessment
    requirement_coverage: list[RenderedRequirementCoverage]
    repair_actions: list[CandidateRepairAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def approval_matches_review_evidence(self) -> CandidateApplicationReview:
        if self.ats_breakdown is not None and self.ats_score != self.ats_breakdown.score:
            raise ValueError("ats_score must match the deterministic ATS breakdown")
        if self.approved:
            if self.blocking_issues or self.repair_actions:
                raise ValueError("approved candidate review cannot contain blockers or repairs")
            if any(state != "pass" for state in self.letter_argument.states):
                raise ValueError("approved candidate review requires a complete letter argument")
        elif not any(issue.strip() for issue in self.blocking_issues) or not self.repair_actions:
            raise ValueError("rejected candidate review requires a blocker and repair action")
        if "repair" in self.letter_argument.states and not any(
            action.surface in {"letter", "both"} for action in self.repair_actions
        ):
            raise ValueError("a deficient letter argument requires a letter repair action")
        return self


BenchmarkReviewLens = Literal["recruiter", "ats_evidence", "editorial"]


class BlindBenchmarkReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    lens: BenchmarkReviewLens
    preferred_variant: Literal["A", "B", "tie"]
    variant_a_score: int = Field(ge=0, le=100)
    variant_b_score: int = Field(ge=0, le=100)
    variant_a_blocking_issues: list[str]
    variant_b_blocking_issues: list[str]
    rationale: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def preference_matches_scores(self) -> BlindBenchmarkReview:
        if self.preferred_variant == "A" and self.variant_a_score <= self.variant_b_score:
            raise ValueError("variant A preference requires a strictly higher score")
        if self.preferred_variant == "B" and self.variant_b_score <= self.variant_a_score:
            raise ValueError("variant B preference requires a strictly higher score")
        return self


class ApplicationDraft(BaseModel):
    cv: CvDraft
    letter: LetterDraft


class OfferRequirement(BaseModel):
    requirement_id: str
    requirement: str
    source_excerpt: str = Field(min_length=1)
    priority: Literal["must", "important", "nice"]
    matching_mode: Literal["exact_term", "semantic_concept", "structured_field"] = (
        "semantic_concept"
    )
    ats_terms: list[str] = Field(default_factory=list, max_length=12)
    kind: Literal[
        "technical_skill",
        "professional_skill",
        "mission",
        "experience",
        "education",
        "domain",
        "professional_behavior",
        "other",
    ]

    @field_validator("kind", mode="before")
    @classmethod
    def technical_subtypes_use_the_requirement_taxonomy(cls, value: object) -> object:
        if value in {"framework", "platform", "library", "tool", "cloud_service"}:
            return "technical_skill"
        return value

    @field_validator("requirement_id")
    @classmethod
    def requirement_id_is_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("requirement_id must not be blank")
        return value

    @field_validator("requirement")
    @classmethod
    def requirement_is_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("requirement must not be blank")
        return value

    @field_validator("source_excerpt")
    @classmethod
    def source_excerpt_is_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("source_excerpt must not be blank")
        return value

    @field_validator("ats_terms")
    @classmethod
    def ats_terms_are_compact_and_unique(cls, values: list[str]) -> list[str]:
        compact = [" ".join(value.split()) for value in values]
        if any(not value for value in compact):
            raise ValueError("ats_terms must not contain blank values")
        if len({value.casefold() for value in compact}) != len(compact):
            raise ValueError("ats_terms must be unique")
        return compact

    @model_validator(mode="after")
    def exact_term_matching_names_terms(self) -> OfferRequirement:
        if self.matching_mode == "exact_term" and not self.ats_terms:
            raise ValueError("exact_term requirements require at least one ATS term")
        return self


class EvidenceMapping(BaseModel):
    requirement_id: str
    evidence_level: Literal["verified", "transferable", "prepared", "unsupported"]
    fact_ids: list[str] = Field(default_factory=list)
    rationale: str

    @field_validator("requirement_id")
    @classmethod
    def requirement_id_is_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("requirement_id must not be blank")
        return value

    @field_validator("rationale")
    @classmethod
    def rationale_is_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("rationale must not be blank")
        return value

    @model_validator(mode="after")
    def evidence_level_matches_support(self) -> EvidenceMapping:
        if self.evidence_level == "verified" and not self.fact_ids:
            raise ValueError("verified evidence requires at least one fact_id")
        if self.evidence_level != "verified" and not self.fact_ids and len(self.rationale) < 10:
            raise ValueError("evidence without fact_ids requires a substantive rationale")
        return self


class AdaptationDecision(BaseModel):
    surface: Literal["cv", "letter", "both"]
    decision: str
    rationale: str
    fact_ids: list[str] = Field(default_factory=list)

    @field_validator("decision", "rationale")
    @classmethod
    def decision_fields_are_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("decision and rationale must not be blank")
        return value


BriefFieldName = Literal[
    "company",
    "role",
    "role_family",
    "language",
    "summary",
    "responsibilities",
    "required_skills",
    "preferred_skills",
    "company_details",
    "seniority",
    "normalized_role",
    "targeted_keywords",
    "cv_angle",
    "letter_angle",
    "adaptation_guidance",
    "open_role",
    "sector",
    "specialisations",
    "requirements",
    "evidence_mappings",
    "adaptation_decisions",
    "project_plan",
    "skill_plan",
    "baseline_cv_assessment",
]


class BriefContractViolation(ValueError):
    """Structured application-brief failure with an explicit repair scope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        repair_fields: tuple[BriefFieldName, ...],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.repair_fields = repair_fields


class BriefRepairAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: BriefFieldName
    problem: str = Field(min_length=10, max_length=800)
    instruction: str = Field(min_length=10, max_length=1200)
    must_preserve: list[BriefFieldName] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def repaired_field_is_not_preserved(self) -> BriefRepairAction:
        if self.field in self.must_preserve:
            raise ValueError("brief repair field cannot also be listed in must_preserve")
        return self


class BriefRequirementAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    requirement_id: str = Field(min_length=1)
    state: Literal["pass", "warning", "repair"]
    rationale: str = Field(min_length=10, max_length=800)


class ApplicationBriefReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool
    score: int = Field(ge=0, le=100)
    blocking_issues: list[str]
    improvements: list[str]
    repair_actions: list[BriefRepairAction]
    requirement_audit: list[BriefRequirementAssessment] = Field(min_length=1)

    @model_validator(mode="after")
    def review_state_is_consistent(self) -> ApplicationBriefReview:
        if self.approved:
            if self.blocking_issues or self.repair_actions:
                raise ValueError("approved brief review cannot contain blockers or repair actions")
            if any(item.state == "repair" for item in self.requirement_audit):
                raise ValueError("approved brief review cannot contain a repair requirement audit")
            return self
        if (
            not any(issue.strip() for issue in self.blocking_issues)
            or not any(improvement.strip() for improvement in self.improvements)
            or not self.repair_actions
        ):
            raise ValueError(
                "rejected brief review requires an issue, improvement, and repair action"
            )
        return self


ProjectDecisionMode = Literal["none", "reuse", "reframe", "derive", "create"]
ProjectSlotMode = Literal["reuse", "reframe", "derive", "create"]
SkillItemKind = Literal[
    "language",
    "tool",
    "platform",
    "framework",
    "method",
    "technical_capability",
    "domain_knowledge",
    "standard",
    "certification",
    "professional_method",
]


class ProjectSlotPlan(BaseModel):
    slot: int = Field(ge=1, le=MAX_VISIBLE_PROJECTS)
    mode: ProjectSlotMode
    source_project_id: str | None = Field(default=None, max_length=80)
    requirement_ids: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(min_length=10, max_length=500)
    requires_external_inspiration: bool = False

    @model_validator(mode="after")
    def source_matches_mode(self) -> ProjectSlotPlan:
        if self.mode in {"reuse", "reframe", "derive"} and not self.source_project_id:
            raise ValueError(f"{self.mode} project slot requires source_project_id")
        if self.mode == "create" and self.source_project_id:
            raise ValueError("create project slot cannot claim a source_project_id")
        return self


class ProjectPlan(BaseModel):
    decision: ProjectDecisionMode
    rationale: str = Field(min_length=10, max_length=600)
    central_gaps: list[str] = Field(default_factory=list, max_length=12)
    slots: list[ProjectSlotPlan] = Field(default_factory=list, max_length=MAX_VISIBLE_PROJECTS)

    @model_validator(mode="after")
    def project_slots_are_coherent(self) -> ProjectPlan:
        if not self.slots:
            if self.decision != "none":
                raise ValueError("an empty project plan requires decision=none")
            if any(gap.strip() for gap in self.central_gaps):
                raise ValueError("an empty project plan cannot claim project gaps")
            return self
        if self.decision == "none":
            raise ValueError("decision=none cannot contain project slots")
        slots = [slot.slot for slot in self.slots]
        expected_slots = list(range(1, len(slots) + 1))
        if slots != expected_slots or len(set(slots)) != len(slots):
            raise ValueError("project plan slots must be unique, ordered and consecutive from 1")
        source_ids = [
            slot.source_project_id for slot in self.slots if slot.source_project_id is not None
        ]
        duplicate_sources = sorted(
            {source_id for source_id in source_ids if source_ids.count(source_id) > 1}
        )
        if duplicate_sources:
            raise ValueError(
                "each visible project slot requires a distinct source_project_id: "
                f"{duplicate_sources}"
            )
        rank = {"reuse": 0, "reframe": 1, "derive": 2, "create": 3}
        strongest = max(self.slots, key=lambda slot: rank[slot.mode]).mode
        self.decision = strongest
        adaptive_slots = [slot for slot in self.slots if slot.mode in {"derive", "create"}]
        if adaptive_slots and not any(gap.strip() for gap in self.central_gaps):
            raise ValueError("derived or created project plan requires a central gap")
        missing_requirement_links = [
            slot.slot for slot in adaptive_slots if not slot.requirement_ids
        ]
        if missing_requirement_links:
            raise ValueError(
                "each derived or created project slot must address a central requirement: "
                f"slots={missing_requirement_links}"
            )
        return self


class SkillPlanItem(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    category: str = Field(min_length=2, max_length=50)
    kind: SkillItemKind
    priority: Literal["must", "important", "nice", "baseline"]
    evidence_level: Literal["verified", "transferable", "prepared", "unsupported"]
    requirement_ids: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("name", "category")
    @classmethod
    def skill_plan_text_is_single_line(cls, value: str) -> str:
        compact = " ".join(value.split())
        if not compact:
            raise ValueError("skill plan fields must not be blank")
        return compact


class SkillPlan(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=4)
    items: list[SkillPlanItem] = Field(min_length=1, max_length=32)
    rationale: str = Field(min_length=10, max_length=600)

    @model_validator(mode="after")
    def items_belong_to_declared_categories(self) -> SkillPlan:
        normalized_categories = [" ".join(category.split()) for category in self.categories]
        if len(set(normalized_categories)) != len(normalized_categories):
            raise ValueError("skill plan categories must be unique")
        unknown = sorted(
            {item.category for item in self.items if item.category not in normalized_categories}
        )
        if unknown:
            raise ValueError(f"skill items reference undeclared categories: {unknown}")
        names = [item.name.casefold() for item in self.items]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"skill plan items must be unique: {duplicates}")
        unsupported = sorted(
            item.name for item in self.items if item.evidence_level == "unsupported"
        )
        if unsupported:
            raise ValueError(
                f"unsupported skills belong in evidence analysis, not the visible skill plan: {unsupported}"
            )
        self.categories = normalized_categories
        return self


class BaselineCvAssessment(BaseModel):
    """Comparable ATS review of the unmodified candidate CV."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["keep_baseline", "adapt"]
    ats_score: int = Field(ge=0, le=100)
    ats_breakdown: AtsScoreBreakdown | None = None
    confidence: Literal["low", "medium", "high"]
    role_positioning_matches: bool
    language_matches: bool
    material_gaps: list[str] = Field(default_factory=list, max_length=20)
    improvable_requirement_ids: list[str] = Field(default_factory=list, max_length=30)
    requirement_coverage: list[RenderedRequirementCoverage] = Field(min_length=1)
    rationale: str = Field(min_length=20, max_length=1000)

    @model_validator(mode="after")
    def keep_decision_requires_strong_evidence(self) -> BaselineCvAssessment:
        if self.ats_breakdown is not None and self.ats_score != self.ats_breakdown.score:
            raise ValueError("ats_score must match the deterministic ATS breakdown")
        if self.decision == "keep_baseline" and (
            self.confidence != "high"
            or not self.role_positioning_matches
            or not self.language_matches
            or any(gap.strip() for gap in self.material_gaps)
            or self.improvable_requirement_ids
        ):
            raise ValueError(
                "keeping the baseline CV requires high confidence, correct positioning and "
                "language, no material gaps, and no improvable requirements"
            )
        return self


class ApplicationBrief(OfferAnalysis):
    language: Literal["fr", "en"]
    open_role: str
    sector: str
    specialisations: list[str] = Field(default_factory=list)
    requirements: list[OfferRequirement] = Field(min_length=1)
    evidence_mappings: list[EvidenceMapping] = Field(min_length=1)
    adaptation_decisions: list[AdaptationDecision] = Field(min_length=1)
    project_plan: ProjectPlan
    skill_plan: SkillPlan
    baseline_cv_assessment: BaselineCvAssessment | None = None

    @field_validator("open_role", "sector")
    @classmethod
    def role_fields_are_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("open_role and sector must not be blank")
        return value

    @field_validator("specialisations")
    @classmethod
    def specialisations_are_non_blank(cls, values: list[str]) -> list[str]:
        stripped = [value.strip() for value in values]
        if any(not value for value in stripped):
            raise ValueError("specialisations must not contain blank items")
        return stripped


def validate_requirement_evidence_contract(brief: ApplicationBrief) -> None:
    """Validate the sourced offer requirements independently of presentation plans."""
    requirement_ids = [requirement.requirement_id for requirement in brief.requirements]
    mapping_ids = [mapping.requirement_id for mapping in brief.evidence_mappings]
    duplicate_requirements = sorted(
        {
            requirement_id
            for requirement_id in requirement_ids
            if requirement_ids.count(requirement_id) > 1
        }
    )
    if duplicate_requirements:
        raise BriefContractViolation(
            "duplicate_requirements",
            f"duplicate requirement_id in requirements: {duplicate_requirements}",
            repair_fields=("requirements", "evidence_mappings", "project_plan", "skill_plan"),
        )
    duplicate_mappings = sorted(
        {requirement_id for requirement_id in mapping_ids if mapping_ids.count(requirement_id) > 1}
    )
    if duplicate_mappings:
        raise BriefContractViolation(
            "duplicate_evidence_mappings",
            f"duplicate requirement_id in evidence_mappings: {duplicate_mappings}",
            repair_fields=("evidence_mappings",),
        )
    requirement_id_set = set(requirement_ids)
    mapping_id_set = set(mapping_ids)
    if requirement_id_set != mapping_id_set:
        missing_ids = sorted(requirement_id_set - mapping_id_set)
        unknown_ids = sorted(mapping_id_set - requirement_id_set)
        raise BriefContractViolation(
            "evidence_mapping_coverage",
            "evidence mappings must contain exactly one EvidenceMapping per requirement; "
            f"missing requirement_id values: {missing_ids}; "
            f"unknown requirement_id values: {unknown_ids}",
            repair_fields=("evidence_mappings",),
        )


def prewrite_fit_gaps(brief: ApplicationBrief) -> list[dict[str, str]]:
    """Return mandatory unsupported gaps that document adaptation cannot repair."""
    evidence_by_id = {mapping.requirement_id: mapping for mapping in brief.evidence_mappings}
    return [
        {
            "requirement_id": requirement.requirement_id,
            "requirement": requirement.requirement,
            "source_excerpt": requirement.source_excerpt,
            "kind": requirement.kind,
            "rationale": evidence_by_id[requirement.requirement_id].rationale,
        }
        for requirement in brief.requirements
        if requirement.priority == "must"
        and requirement.requirement_id in evidence_by_id
        and evidence_by_id[requirement.requirement_id].evidence_level == "unsupported"
        and not evidence_by_id[requirement.requirement_id].fact_ids
    ]


def validate_application_brief_contract(brief: ApplicationBrief) -> None:
    validate_requirement_evidence_contract(brief)
    requirement_ids = [requirement.requirement_id for requirement in brief.requirements]
    requirement_id_set = set(requirement_ids)
    project_requirement_ids = {
        requirement_id
        for slot in brief.project_plan.slots
        for requirement_id in slot.requirement_ids
    }
    skill_requirement_ids = {
        requirement_id for item in brief.skill_plan.items for requirement_id in item.requirement_ids
    }
    unknown_project_ids = sorted(project_requirement_ids - requirement_id_set)
    unknown_skill_ids = sorted(skill_requirement_ids - requirement_id_set)
    unknown_plan_ids = sorted(set(unknown_project_ids) | set(unknown_skill_ids))
    if unknown_plan_ids:
        repair_field_values: list[BriefFieldName] = []
        if unknown_project_ids:
            repair_field_values.append("project_plan")
        if unknown_skill_ids:
            repair_field_values.append("skill_plan")
        raise BriefContractViolation(
            "unknown_plan_requirement_ids",
            f"project/skill plans reference unknown requirement_id values: {unknown_plan_ids}",
            repair_fields=tuple(repair_field_values),
        )
    mappings_by_id = {mapping.requirement_id: mapping for mapping in brief.evidence_mappings}
    missing_central_technical = sorted(
        requirement.requirement_id
        for requirement in brief.requirements
        if requirement.kind in {"technical_skill", "professional_skill"}
        and requirement.priority in {"must", "important"}
        and mappings_by_id[requirement.requirement_id].evidence_level != "unsupported"
        and requirement.requirement_id not in skill_requirement_ids
    )
    if missing_central_technical:
        raise BriefContractViolation(
            "missing_central_hard_skill_coverage",
            "skill_plan must represent supported central hard-skill requirements: "
            f"{missing_central_technical}",
            repair_fields=("skill_plan",),
        )
    if brief.baseline_cv_assessment is None:
        raise BriefContractViolation(
            "missing_baseline_cv_assessment",
            "application brief requires a baseline CV assessment before document writing",
            repair_fields=("baseline_cv_assessment",),
        )
    baseline_ids = [
        item.requirement_id for item in brief.baseline_cv_assessment.requirement_coverage
    ]
    if set(baseline_ids) != requirement_id_set or len(baseline_ids) != len(set(baseline_ids)):
        raise BriefContractViolation(
            "baseline_cv_requirement_coverage",
            "baseline CV assessment must cover every requirement exactly once",
            repair_fields=("baseline_cv_assessment",),
        )
    unknown_improvable = sorted(
        set(brief.baseline_cv_assessment.improvable_requirement_ids) - requirement_id_set
    )
    if unknown_improvable:
        raise BriefContractViolation(
            "baseline_cv_unknown_improvable_requirement",
            f"baseline CV assessment references unknown requirement IDs: {unknown_improvable}",
            repair_fields=("baseline_cv_assessment",),
        )
    if brief.baseline_cv_assessment.decision == "keep_baseline":
        coverage_by_id = {
            item.requirement_id: item.coverage
            for item in brief.baseline_cv_assessment.requirement_coverage
        }
        evidence_by_id = {mapping.requirement_id: mapping for mapping in brief.evidence_mappings}
        weak_supported_central = sorted(
            requirement.requirement_id
            for requirement in brief.requirements
            if requirement.priority in {"must", "important"}
            and evidence_by_id[requirement.requirement_id].evidence_level != "unsupported"
            and coverage_by_id[requirement.requirement_id] in {"indirect", "missing"}
        )
        if weak_supported_central:
            raise BriefContractViolation(
                "baseline_cv_weak_central_coverage",
                "baseline CV cannot be kept while supported central requirements have weak "
                f"coverage: {weak_supported_central}",
                repair_fields=("baseline_cv_assessment",),
            )


class BriefFieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: BriefFieldName
    value: Any


class ApplicationBriefPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    base_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    updates: list[BriefFieldUpdate] = Field(default_factory=list, max_length=24)
    resolved_fields: list[BriefFieldName] = Field(default_factory=list, max_length=24)

    @model_validator(mode="after")
    def patch_is_non_empty_and_consistent(self) -> ApplicationBriefPatch:
        if not self.updates:
            raise ValueError("brief patch requires at least one update")
        changed = [update.field for update in self.updates]
        if len(set(changed)) != len(changed):
            raise ValueError("brief patch fields must be unique")
        if len(set(self.resolved_fields)) != len(self.resolved_fields):
            raise ValueError("resolved brief patch fields must be unique")
        if set(changed) != set(self.resolved_fields):
            raise ValueError("resolved_fields must match brief patch update fields")
        return self

    @property
    def changed_fields(self) -> set[BriefFieldName]:
        return {update.field for update in self.updates}


class AgenticCvDraft(CvDraft):
    experience_bullets: list[str] = Field(min_length=3, max_length=3)
    internal_project_bullets: list[str] = Field(min_length=2, max_length=2)
    selected_projects: list[str] = Field(default_factory=list, max_length=0)
    project_entries: list[ProjectDraft] = Field(min_length=2, max_length=3)
    skill_groups: dict[str, list[str]] = Field(default_factory=dict, max_length=0)
    skill_sections: dict[str, list[str]] = Field(min_length=3, max_length=4)
    internal_project_title: str = Field(min_length=3, max_length=120)
    internal_project_stack: str = Field(min_length=3, max_length=180)

    @field_validator("internal_project_title", "internal_project_stack")
    @classmethod
    def internal_project_heading_is_single_line(cls, value: str) -> str:
        if "\n" in value:
            raise ValueError("internal project heading fields must stay on one line")
        return value.strip()


class AgenticApplicationPackage(ApplicationDraft):
    cv: AgenticCvDraft
    letter: AgenticLetterDraft
    brief: ApplicationBrief

    @field_validator("cv", mode="before")
    @classmethod
    def accept_validated_cv_input(cls, cv: object) -> object:
        if isinstance(cv, CvDraft) and not isinstance(cv, AgenticCvDraft):
            return cv.model_dump()
        return cv

    @field_validator("letter", mode="before")
    @classmethod
    def accept_validated_letter_input(cls, letter: object) -> object:
        if isinstance(letter, LetterDraft) and not isinstance(letter, AgenticLetterDraft):
            return letter.model_dump()
        return letter


class AgenticApplicationDraft(ApplicationDraft):
    offer_analysis: OfferAnalysis | None = None
    generation_notes: list[str] = Field(default_factory=list)
