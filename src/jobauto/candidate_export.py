from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

import yaml

from jobauto.adaptation_policy import FidelityLevel, SectionPolicy
from jobauto.candidate_draft import CandidateDraft, DraftOrigin, DraftStatus, SkillEvidence
from jobauto.candidate_form_profile import (
    CandidateFormEducation,
    CandidateFormExperience,
    CandidateFormProfile,
)
from jobauto.candidate_profile import CvBackend
from jobauto.candidate_snapshot import CandidateProfileRepository, CandidateSnapshot
from jobauto.generated_cv_template import generated_cv_template_bytes
from jobauto.latex_cv_source import LatexCvMapping, TexBlockKind

_DEFAULT_SECTION_POLICIES = {
    "identity": SectionPolicy(fidelity=FidelityLevel.LOCKED),
    "summary": SectionPolicy(fidelity=FidelityLevel.ADAPTABLE),
    "experience": SectionPolicy(fidelity=FidelityLevel.VERY_FAITHFUL),
    "projects": SectionPolicy(fidelity=FidelityLevel.HIGHLY_ADAPTABLE),
    "skills": SectionPolicy(fidelity=FidelityLevel.REPLACEABLE),
    "education": SectionPolicy(fidelity=FidelityLevel.LOCKED),
    "languages": SectionPolicy(fidelity=FidelityLevel.LOCKED),
    "interests": SectionPolicy(fidelity=FidelityLevel.VERY_FAITHFUL, required=False),
}
_DEFAULT_OTHER_POLICY = SectionPolicy(fidelity=FidelityLevel.VERY_FAITHFUL)


def export_candidate_draft(
    *,
    draft: CandidateDraft,
    tex_source: bytes,
    mapping: LatexCvMapping,
    profiles_root: Path,
) -> tuple[Path, CandidateSnapshot]:
    if draft.status is not DraftStatus.VALIDATED:
        raise ValueError("candidate draft must be validated before export")
    if draft.source_sha256 != mapping.source_sha256:
        raise ValueError("candidate draft source hash does not match mapping")
    if draft.mapping_hash != mapping.mapping_hash:
        raise ValueError("candidate draft mapping changed after validation")

    exported_mapping = _mapping_with_semantic_kinds(draft, mapping)
    source_for_profile = (
        tex_source if draft.origin is DraftOrigin.LATEX else generated_cv_template_bytes()
    )

    root = profiles_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate_id = _candidate_id(draft)
    target = root / candidate_id
    if target.exists():
        _write_studio_source(target, draft, candidate_id)
        profile_path = target / "profile.yaml"
        snapshot = CandidateProfileRepository(root).load_snapshot(profile_path)
        return profile_path, snapshot
    temporary = Path(tempfile.mkdtemp(dir=root, prefix=f".{candidate_id}."))
    try:
        _write_bytes(temporary / "cv_source.tex", source_for_profile)
        if draft.origin is DraftOrigin.LATEX:
            exported_mapping.write(temporary / "cv_mapping.json")
        _write_text(temporary / "cv_source.md", _cv_source_markdown(draft))
        _write_text(temporary / "letter_model.txt", draft.letter_reference or "")
        _write_yaml(temporary / "facts.yaml", _facts_payload(draft))
        _write_yaml(temporary / "projects.yaml", _projects_payload(draft))
        _write_yaml(temporary / "skills.yaml", _skills_payload(draft))
        _write_yaml(
            temporary / "adaptation_policy.yaml",
            (
                _adaptation_policy_payload(draft, exported_mapping)
                if draft.origin is DraftOrigin.LATEX
                else _generated_adaptation_policy_payload(draft)
            ),
        )
        _write_yaml(
            temporary / "search_preferences.yaml",
            draft.search_preferences.model_dump(mode="json"),
        )
        _write_yaml(
            temporary / "submission_preferences.yaml",
            draft.submission_preferences.model_dump(mode="json"),
        )
        _write_text(
            temporary / "form_profile.json",
            _form_profile(draft).model_dump_json(indent=2) + "\n",
        )
        _write_studio_source(temporary, draft, candidate_id)
        _write_yaml(temporary / "profile.yaml", _profile_payload(draft, candidate_id))
        CandidateProfileRepository(root).load_snapshot(temporary / "profile.yaml")
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    snapshot = CandidateProfileRepository(root).load_snapshot(target / "profile.yaml")
    return target / "profile.yaml", snapshot


def _write_studio_source(target: Path, draft: CandidateDraft, candidate_id: str) -> None:
    _write_text(
        target / "studio_source.json",
        json.dumps(
            {
                "candidate_id": candidate_id,
                "draft_id": draft.draft_id,
                "import_id": draft.import_id,
                "source_sha256": draft.source_sha256,
                "mapping_hash": draft.mapping_hash,
                "origin": draft.origin.value,
                "source_document_id": draft.source_document_id,
                "source_document_filename": draft.source_document_filename,
                "source_document_sha256": draft.source_document_sha256,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )


def _candidate_id(draft: CandidateDraft) -> str:
    name = f"{draft.identity.first_name or ''}-{draft.identity.last_name or ''}"
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", ascii_name)).strip("-")
    slug = slug or "candidate"
    return f"{slug[:60]}-{draft.draft_id[:8]}-v{draft.version}"


def _profile_payload(draft: CandidateDraft, candidate_id: str) -> dict[str, object]:
    protected = [
        f"experience.{experience.experience_id}.metric.{index + 1}"
        for experience in draft.experiences
        for index, _metric in enumerate(experience.metrics)
    ]
    return {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "identity": {
            "first_name": draft.identity.first_name,
            "last_name": draft.identity.last_name,
            "email": draft.identity.email,
            "phone": draft.identity.phone,
            "location": draft.identity.location,
        },
        "locale": draft.locale,
        "facts_path": "facts.yaml",
        "project_bank_path": "projects.yaml",
        "skill_policy_path": "skills.yaml",
        "cv_backend": (
            CvBackend.SOURCE_PRESERVING.value
            if draft.origin is DraftOrigin.LATEX
            else CvBackend.GENERATED_TEMPLATE.value
        ),
        "cv_model_path": "cv_source.tex",
        **({"cv_mapping_path": "cv_mapping.json"} if draft.origin is DraftOrigin.LATEX else {}),
        "cv_source_path": "cv_source.md",
        "letter_model_path": "letter_model.txt",
        "adaptation_policy_path": "adaptation_policy.yaml",
        "search_preferences_path": "search_preferences.yaml",
        "submission_preferences_path": "submission_preferences.yaml",
        "form_profile_path": "form_profile.json",
        "protected_claims": protected,
        "forbidden_claims": [],
        "project_lab": draft.project_lab.model_dump(mode="json"),
    }


def _form_profile(draft: CandidateDraft) -> CandidateFormProfile:
    return CandidateFormProfile(
        experiences=[
            CandidateFormExperience(
                organization=item.organization,
                role=item.role,
                location=item.location,
                dates=item.dates,
                description=[*item.facts, *item.metrics],
            )
            for item in draft.experiences
        ],
        education=[
            CandidateFormEducation(
                institution=_clean_form_text(item.get("institution")),
                program=_clean_form_text(item.get("program")),
                location=(_clean_form_text(item.get("location")) or None),
                dates=(_clean_form_text(item.get("dates")) or None),
                details=[
                    text for detail in item.get("details", []) if (text := _clean_form_text(detail))
                ],
            )
            for item in draft.education
            if _clean_form_text(item.get("institution")) and _clean_form_text(item.get("program"))
        ],
        languages=draft.languages,
    )


def _clean_form_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _facts_payload(draft: CandidateDraft) -> dict[str, object]:
    role_tags = [
        *draft.search_preferences.roles.required,
        *draft.search_preferences.roles.preferred,
    ]
    facts: list[dict[str, object]] = []
    if draft.summary or draft.identity.headline:
        facts.append(
            {
                "fact_id": "profile.summary",
                "claim": draft.summary or draft.identity.headline,
                "status": "verified",
                "role_tags": role_tags,
                "keywords": [],
            }
        )
    for experience in draft.experiences:
        if experience.dates:
            facts.append(
                {
                    "fact_id": f"experience.{experience.experience_id}.dates",
                    "claim": (f"{experience.organization} - {experience.role}: {experience.dates}"),
                    "status": "verified",
                    "role_tags": [*role_tags, *experience.allowed_angles],
                    "keywords": experience.tools,
                }
            )
        for label, values in (("fact", experience.facts), ("metric", experience.metrics)):
            for index, claim in enumerate(values, start=1):
                facts.append(
                    {
                        "fact_id": f"experience.{experience.experience_id}.{label}.{index}",
                        "claim": claim,
                        "status": "verified",
                        "role_tags": [*role_tags, *experience.allowed_angles],
                        "keywords": experience.tools,
                    }
                )
    for index, section in enumerate(draft.additional_sections, start=1):
        facts.append(
            {
                "fact_id": f"additional.{index}",
                "claim": f"{section.label}: {section.content}",
                "status": "verified",
                "role_tags": role_tags,
                "keywords": [],
            }
        )
    if not facts:
        facts.append(
            {
                "fact_id": "identity.confirmed",
                "claim": f"{draft.identity.first_name} {draft.identity.last_name}",
                "status": "verified",
                "role_tags": role_tags,
                "keywords": [],
            }
        )
    return {"facts": facts}


def _projects_payload(draft: CandidateDraft) -> dict[str, object]:
    role_fit = [
        *draft.search_preferences.roles.required,
        *draft.search_preferences.roles.preferred,
    ] or ["general"]
    projects = []
    for project in draft.projects:
        keywords = list(dict.fromkeys([*project.stack, *project.title.split()]))
        projects.append(
            {
                "id": _project_bank_id(project.project_id),
                "title": project.title,
                "status": "validated_private",
                "visibility": "cv_project" if project.visible_by_default else "context",
                "role_fit": role_fit,
                "keywords": keywords,
                "verified_stack": project.stack,
                "transferable_keywords": [],
                "default_stack_line": ", ".join(project.stack),
                "cv_bullets": project.description,
                "letter_angles": [],
                "avoid_if": [],
                "warnings": [],
                "use_mode": project.use_mode.value,
                "title_fidelity": project.title_fidelity.value,
                "stack_fidelity": project.stack_fidelity.value,
                "description_fidelity": project.description_fidelity.value,
            }
        )
    return {"projects": projects}


def _project_bank_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if len(normalized) < 2:
        raise ValueError(f"project id cannot be normalized safely: {value!r}")
    return normalized[:80]


def _skills_payload(draft: CandidateDraft) -> dict[str, object]:
    grouped: dict[str, dict[str, list[str]]] = {evidence.value: {} for evidence in SkillEvidence}
    usage: dict[str, list[str]] = {}
    warnings: list[str] = []
    for skill in draft.skills:
        grouped[skill.evidence.value].setdefault(skill.category, []).append(skill.name)
        usage.setdefault(skill.usage.value, []).append(skill.name)
        if skill.verification_warning:
            warnings.append(skill.name)
    transferable = {
        f"group_{index + 1}": {"group": group, "skills": skills}
        for index, (group, skills) in enumerate(grouped[SkillEvidence.TRANSFERABLE.value].items())
    }
    return {
        "minimum_group_overlap": 0.0,
        "verified": grouped[SkillEvidence.VERIFIED.value],
        "transferable": transferable,
        "forbidden": grouped[SkillEvidence.FORBIDDEN.value],
        "usage": usage,
        "warnings": warnings,
    }


def _adaptation_policy_payload(draft: CandidateDraft, mapping: LatexCvMapping) -> dict[str, object]:
    mapped: dict[str, SectionPolicy] = {}
    section_order: list[str] = []
    for block in mapping.blocks:
        section = _section_for_kind(block.kind)
        if section and section not in mapped:
            mapped[section] = block.policy
            section_order.append(section)
    sections = {
        name: (mapped.get(name) or _DEFAULT_SECTION_POLICIES[name]).model_dump(mode="json")
        for name in section_order
    }
    if "identity" in sections:
        sections["identity"]["protected_terms"] = [
            f"{draft.identity.first_name} {draft.identity.last_name}"
        ]
    metric_ids = [
        f"experience.{experience.experience_id}.metric.{index + 1}"
        for experience in draft.experiences
        if "metrics" in experience.protected_fields
        for index, _metric in enumerate(experience.metrics)
    ]
    if "experience" in sections:
        sections["experience"]["protected_fact_ids"] = metric_ids
    return {
        "schema_version": 1,
        "policy_id": f"studio-{draft.draft_id[:12]}",
        "documents": {
            "cv": {
                "section_order": section_order,
                "sections": sections,
                "layout": draft.cv_layout.model_dump(mode="json"),
            },
            "letter": {
                "section_order": ["body"],
                "sections": {
                    "body": {
                        "fidelity": FidelityLevel.HIGHLY_ADAPTABLE.value,
                        "required": True,
                        "max_characters": 20_000,
                    }
                },
            },
        },
    }


def _generated_adaptation_policy_payload(draft: CandidateDraft) -> dict[str, object]:
    section_order = ["identity"]
    if draft.summary:
        section_order.append("summary")
    if draft.experiences:
        section_order.append("experience")
    if any(project.visible_by_default for project in draft.projects):
        section_order.append("projects")
    if any(skill.evidence is not SkillEvidence.FORBIDDEN for skill in draft.skills):
        section_order.append("skills")
    if draft.education:
        section_order.append("education")
    section_order.extend(
        f"additional_{index}" for index, _section in enumerate(draft.additional_sections)
    )
    if draft.languages:
        section_order.append("languages")
    if draft.interests:
        section_order.append("interests")

    sections: dict[str, dict[str, object]] = {}
    for name in section_order:
        if name.startswith("additional_"):
            index = int(name.removeprefix("additional_"))
            policy = SectionPolicy(fidelity=draft.additional_sections[index].fidelity)
        else:
            policy = _DEFAULT_SECTION_POLICIES[name]
        sections[name] = policy.model_dump(mode="json")
    sections["identity"]["protected_terms"] = [
        f"{draft.identity.first_name} {draft.identity.last_name}"
    ]
    metric_ids = [
        f"experience.{experience.experience_id}.metric.{index + 1}"
        for experience in draft.experiences
        if "metrics" in experience.protected_fields
        for index, _metric in enumerate(experience.metrics)
    ]
    if "experience" in sections:
        sections["experience"]["protected_fact_ids"] = metric_ids
    return {
        "schema_version": 1,
        "policy_id": f"studio-{draft.draft_id[:12]}",
        "documents": {
            "cv": {
                "section_order": section_order,
                "sections": sections,
                "layout": draft.cv_layout.model_dump(mode="json"),
            },
            "letter": {
                "section_order": ["body"],
                "sections": {
                    "body": {
                        "fidelity": FidelityLevel.HIGHLY_ADAPTABLE.value,
                        "required": True,
                        "max_characters": 20_000,
                    }
                },
            },
        },
    }


def _mapping_with_semantic_kinds(
    draft: CandidateDraft,
    mapping: LatexCvMapping,
) -> LatexCvMapping:
    """Promote unambiguous custom blocks using the extracted candidate semantics."""
    semantic_uses: dict[str, set[TexBlockKind]] = {}

    def register(block_ids: list[str], kind: TexBlockKind) -> None:
        for block_id in block_ids:
            semantic_uses.setdefault(block_id, set()).add(kind)

    register(draft.identity.source_block_ids, TexBlockKind.IDENTITY)
    register(draft.summary_source_block_ids, TexBlockKind.SUMMARY)
    for item in draft.experiences:
        register(item.source_block_ids, TexBlockKind.EXPERIENCE)
    for item in draft.projects:
        register(item.source_block_ids, TexBlockKind.PROJECTS)
    for item in draft.skills:
        register(item.source_block_ids, TexBlockKind.SKILLS)
    for item in draft.education:
        if isinstance(item, dict):
            register(list(item.get("source_block_ids", [])), TexBlockKind.EDUCATION)

    changed = False
    blocks = []
    for block in mapping.blocks:
        kinds = semantic_uses.get(block.block_id, set())
        if block.kind is not TexBlockKind.OTHER or len(kinds) != 1:
            blocks.append(block)
            continue
        kind = next(iter(kinds))
        section = _section_for_kind(kind)
        if section is None:
            blocks.append(block)
            continue
        promoted_policy = (
            _DEFAULT_SECTION_POLICIES[section]
            if block.policy == _DEFAULT_OTHER_POLICY
            else block.policy
        )
        blocks.append(
            block.model_copy(
                update={
                    "kind": kind,
                    "detector": f"{block.detector}+profile-extraction"[:80],
                    "confidence": max(block.confidence, 0.95),
                    "policy": promoted_policy,
                }
            )
        )
        changed = True
    return mapping.model_copy(update={"blocks": blocks}) if changed else mapping


def _section_for_kind(kind: TexBlockKind) -> str | None:
    return {
        TexBlockKind.IDENTITY: "identity",
        TexBlockKind.SUMMARY: "summary",
        TexBlockKind.EXPERIENCE: "experience",
        TexBlockKind.PROJECTS: "projects",
        TexBlockKind.SKILLS: "skills",
        TexBlockKind.EDUCATION: "education",
        TexBlockKind.LANGUAGES: "languages",
        TexBlockKind.INTERESTS: "interests",
        TexBlockKind.OTHER: None,
    }[kind]


def _cv_source_markdown(draft: CandidateDraft) -> str:
    name = f"{draft.identity.first_name} {draft.identity.last_name}".strip()
    contact = [f"Email: {draft.identity.email}"]
    if draft.identity.phone:
        contact.append(f"Phone: {draft.identity.phone}")
    if draft.identity.location:
        contact.append(draft.identity.location)
    lines = [f"# {name}", draft.identity.headline or "Candidate", " | ".join(contact)]
    if draft.summary:
        lines.extend(["", "## Summary", draft.summary])
    if draft.experiences:
        lines.extend(["", "## Experience"])
        for item in draft.experiences:
            title = f"{item.organization} – {item.role}"
            suffix = f" | {item.dates}" if item.dates else ""
            lines.append(f"### {title}{suffix}")
            # Metrics remain verified evidence and protected facts. They are not separate
            # source rows: the imported LaTeX owns the visible bullet structure and often
            # already embeds a metric inside its corresponding experience bullet.
            lines.extend(f"- {fact}" for fact in item.facts)
    visible_projects = [project for project in draft.projects if project.visible_by_default]
    if visible_projects:
        lines.extend(["", "## Projects"])
        for item in visible_projects:
            suffix = f" | {', '.join(item.stack)}" if item.stack else ""
            lines.append(f"### {item.title}{suffix}")
            lines.extend(f"- {description}" for description in item.description)
    visible_skills = [
        skill for skill in draft.skills if skill.evidence is not SkillEvidence.FORBIDDEN
    ]
    if visible_skills:
        lines.extend(["", "## Skills"])
        groups: dict[str, list[str]] = {}
        for skill in visible_skills:
            groups.setdefault(skill.category, []).append(skill.name)
        lines.extend(f"{group}: {', '.join(skills)}" for group, skills in groups.items())
    if draft.education:
        lines.extend(["", "## Education"])
        for item in draft.education:
            title = f"{item.get('institution', '')} – {item.get('program', '')}".strip(" –")
            suffix = f" | {item.get('dates')}" if item.get("dates") else ""
            lines.append(f"### {title}{suffix}")
            lines.extend(f"- {detail}" for detail in item.get("details", []))
    if draft.languages:
        lines.extend(["", "## Languages", " | ".join(draft.languages)])
    if draft.interests:
        lines.extend(["", "## Interests", " | ".join(draft.interests)])
    for section in draft.additional_sections:
        lines.extend(["", f"## {section.label}", section.content])
    return "\n".join(lines).rstrip() + "\n"


def _write_yaml(path: Path, payload: object) -> None:
    _write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_bytes(path: Path, value: bytes) -> None:
    path.write_bytes(value)
