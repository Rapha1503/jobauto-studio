from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jobauto.adaptation_policy import AdaptationPolicy, SectionPolicy, validate_section_change
from jobauto.candidate_snapshot import CandidateSnapshot
from jobauto.cv_source import CvEntry, CvSourceDocument
from jobauto.models import ApplicationBrief, CandidateLetterDraft
from jobauto.source_preserving_cv import LatexCvPatch

CvSectionId = str


class CvFieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=3, max_length=120)
    value: str | list[str]
    fact_ids: list[str] = Field(min_length=1)

    @field_validator("source_id")
    @classmethod
    def source_id_is_structural(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or any(character.isspace() for character in normalized):
            raise ValueError("source_id must be a compact structural path")
        return normalized

    @field_validator("value")
    @classmethod
    def replacement_is_non_empty(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            compact = " ".join(value.split())
            if not compact:
                raise ValueError("replacement text must not be blank")
            return compact
        compact_items = [" ".join(item.split()) for item in value]
        if not compact_items or any(not item for item in compact_items):
            raise ValueError("replacement list must contain non-blank items")
        return compact_items


class CvProjectSectionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[CvEntry] = Field(min_length=1, max_length=4)
    fact_ids: list[str] = Field(min_length=1)


class CvSkillSectionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: dict[str, list[str]] = Field(min_length=1, max_length=5)
    fact_ids: list[str] = Field(min_length=1)

    @field_validator("groups")
    @classmethod
    def groups_are_non_empty(cls, groups: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(not label.strip() or not items for label, items in groups.items()):
            raise ValueError("skill groups require non-blank labels and items")
        return groups


class CvSourceBlockChange(BaseModel):
    """Semantic replacement for one candidate-defined LaTeX section."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^source_block\.[A-Za-z0-9_-]+$")
    value: str = Field(min_length=1, max_length=20_000)
    fact_ids: list[str] = Field(min_length=1)

    @field_validator("value")
    @classmethod
    def value_is_non_empty(cls, value: str) -> str:
        compact = "\n".join(line.strip() for line in value.splitlines() if line.strip())
        if not compact:
            raise ValueError("source block replacement must not be blank")
        return compact


class CvAdaptationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[CvFieldChange] = Field(default_factory=list)
    projects: CvProjectSectionChange | None = None
    skills: CvSkillSectionChange | None = None
    source_blocks: list[CvSourceBlockChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def source_ids_are_unique(self) -> CvAdaptationPatch:
        source_ids = [change.source_id for change in self.changes]
        duplicates = sorted(
            {source_id for source_id in source_ids if source_ids.count(source_id) > 1}
        )
        if duplicates:
            raise ValueError(f"duplicate CV source IDs: {duplicates}")
        custom_ids = [change.source_id for change in self.source_blocks]
        custom_duplicates = sorted(
            {source_id for source_id in custom_ids if custom_ids.count(source_id) > 1}
        )
        if custom_duplicates:
            raise ValueError(f"duplicate custom CV source IDs: {custom_duplicates}")
        overlap = sorted(set(source_ids) & set(custom_ids))
        if overlap:
            raise ValueError(f"CV source IDs appear in multiple patch surfaces: {overlap}")
        return self


@dataclass(frozen=True)
class CvFieldRef:
    source_id: str
    section_id: CvSectionId
    value_kind: Literal["text", "items"] = "text"


@dataclass(frozen=True)
class CvDocumentDraft:
    document: CvSourceDocument
    provenance: Mapping[str, tuple[str, ...]]
    latex_patch: LatexCvPatch | None = None
    source_blocks: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CandidateDocumentDraft:
    brief: ApplicationBrief
    cv_patch: CvAdaptationPatch
    cv: CvDocumentDraft
    letter: CandidateLetterDraft


def merge_cv_adaptation_patch(
    base: CvAdaptationPatch,
    repair: CvAdaptationPatch,
) -> CvAdaptationPatch:
    """Apply a focused repair without regenerating unrelated accepted changes."""
    repair_by_id = {change.source_id: change for change in repair.changes}
    merged_changes = [repair_by_id.pop(change.source_id, change) for change in base.changes]
    merged_changes.extend(repair_by_id.values())
    repair_blocks = {change.source_id: change for change in repair.source_blocks}
    merged_blocks = [repair_blocks.pop(change.source_id, change) for change in base.source_blocks]
    merged_blocks.extend(repair_blocks.values())
    return CvAdaptationPatch(
        changes=merged_changes,
        projects=repair.projects if repair.projects is not None else base.projects,
        skills=repair.skills if repair.skills is not None else base.skills,
        source_blocks=merged_blocks,
    )


def index_cv_source(document: CvSourceDocument) -> dict[str, CvFieldRef]:
    index: dict[str, CvFieldRef] = {
        "headline.text": CvFieldRef("headline.text", "summary"),
        "summary.text": CvFieldRef("summary.text", "summary"),
        "languages.text": CvFieldRef("languages.text", "languages"),
        "interests.text": CvFieldRef("interests.text", "interests"),
    }
    for section_id in ("experience", "projects", "education"):
        entries = getattr(document, section_id)
        for entry_index, entry in enumerate(entries):
            prefix = f"{section_id}.{entry_index}"
            index[f"{prefix}.title"] = CvFieldRef(f"{prefix}.title", section_id)
            if entry.dates is not None:
                index[f"{prefix}.dates"] = CvFieldRef(f"{prefix}.dates", section_id)
            if entry.stack is not None:
                index[f"{prefix}.stack"] = CvFieldRef(f"{prefix}.stack", section_id)
            for bullet_index, _bullet in enumerate(entry.bullets):
                source_id = f"{prefix}.bullet.{bullet_index}"
                index[source_id] = CvFieldRef(source_id, section_id)
    for skill_index, _item in enumerate(document.skills.items()):
        prefix = f"skills.{skill_index}"
        index[f"{prefix}.label"] = CvFieldRef(f"{prefix}.label", "skills")
        index[f"{prefix}.items"] = CvFieldRef(f"{prefix}.items", "skills", value_kind="items")
    for section_index, _section in enumerate(document.additional_sections):
        source_id = f"additional.{section_index}.content"
        index[source_id] = CvFieldRef(source_id, f"additional_{section_index}")
    return index


def editable_cv_source_index(snapshot: CandidateSnapshot) -> dict[str, CvFieldRef]:
    try:
        sections = snapshot.adaptation_policy.documents["cv"].sections
    except KeyError as exc:
        raise ValueError("adaptation policy has no cv document") from exc
    return {
        source_id: field_ref
        for source_id, field_ref in index_cv_source(snapshot.cv_source).items()
        if field_ref.section_id in sections and sections[field_ref.section_id].capabilities.rephrase
    }


def apply_cv_patch(
    snapshot: CandidateSnapshot,
    patch: CvAdaptationPatch,
) -> CvDocumentDraft:
    source = snapshot.cv_source
    policy: AdaptationPolicy = snapshot.adaptation_policy
    allowed_fact_ids = snapshot.evidence_ids
    try:
        cv_policy = policy.documents["cv"]
    except KeyError as exc:
        raise ValueError("adaptation policy has no cv document") from exc
    index = index_cv_source(source)
    draft = deepcopy(source)
    provenance: dict[str, tuple[str, ...]] = {}
    changed_sections: dict[str, list[str]] = {}
    changed_source_blocks: dict[str, str] = {}

    for change in patch.changes:
        try:
            field_ref = index[change.source_id]
        except KeyError as exc:
            raise ValueError(f"Unknown CV source ID: {change.source_id}") from exc
        unknown_facts = sorted(set(change.fact_ids) - allowed_fact_ids)
        if unknown_facts:
            raise ValueError(f"Unknown candidate fact IDs: {unknown_facts}")
        try:
            section_policy = cv_policy.sections[field_ref.section_id]
        except KeyError as exc:
            raise ValueError(
                f"adaptation policy has no section for {field_ref.section_id}"
            ) from exc
        _require_change_allowed(section_policy, field_ref)
        _apply_change(draft, field_ref, change.value)
        fact_ids = tuple(dict.fromkeys(change.fact_ids))
        provenance[change.source_id] = fact_ids
        changed_sections.setdefault(field_ref.section_id, []).extend(fact_ids)

    section_replacements = (
        ("projects", "projects.section", patch.projects),
        ("skills", "skills.section", patch.skills),
    )
    for section_id, source_id, replacement in section_replacements:
        if replacement is None:
            continue
        section_policy = cv_policy.sections[section_id]
        if not _can_recompose_structured_section(section_policy):
            raise ValueError(f"CV section cannot be replaced: {section_id}")
        unknown_facts = sorted(set(replacement.fact_ids) - allowed_fact_ids)
        if unknown_facts:
            raise ValueError(f"Unknown candidate fact IDs: {unknown_facts}")
        if section_id == "projects":
            draft.projects = _fit_project_entries_to_source_shape(
                snapshot,
                deepcopy(replacement.entries),
            )
        else:
            _require_skill_groups_fit_source_shape(snapshot, replacement.groups)
            draft.skills = _without_dedicated_section_duplicates(
                deepcopy(replacement.groups),
                languages=draft.languages,
            )
        fact_ids = tuple(dict.fromkeys(replacement.fact_ids))
        provenance[source_id] = fact_ids
        changed_sections.setdefault(section_id, []).extend(fact_ids)

    mapping = snapshot.cv_mapping
    blocks_by_id = (
        {block.block_id: block for block in mapping.blocks} if mapping is not None else {}
    )
    for replacement in patch.source_blocks:
        block_id = replacement.source_id.removeprefix("source_block.")
        try:
            block = blocks_by_id[block_id]
        except KeyError as exc:
            raise ValueError(f"Unknown custom CV source block: {replacement.source_id}") from exc
        if block.kind.value != "other":
            raise ValueError(
                f"Custom CV source patch must target an additional section: {replacement.source_id}"
            )
        if not block.policy.capabilities.rephrase:
            raise ValueError(f"Custom CV source block is locked: {replacement.source_id}")
        unknown_facts = sorted(set(replacement.fact_ids) - allowed_fact_ids)
        if unknown_facts:
            raise ValueError(f"Unknown candidate fact IDs: {unknown_facts}")
        original = snapshot.cv_template_bytes[block.start_byte : block.end_byte].decode("utf-8")
        violations = validate_section_change(
            block.policy,
            original,
            replacement.value,
            used_fact_ids=replacement.fact_ids,
        )
        if violations:
            details = "; ".join(f"{item.code}: {item.message}" for item in violations)
            raise ValueError(
                f"CV source block {replacement.source_id} violates adaptation policy: {details}"
            )
        changed_source_blocks[replacement.source_id] = replacement.value
        provenance[replacement.source_id] = tuple(dict.fromkeys(replacement.fact_ids))

    for section_id, fact_ids in changed_sections.items():
        section_policy = cv_policy.sections[section_id]
        violations = validate_section_change(
            section_policy,
            _section_text(source, section_id),
            _section_text(draft, section_id),
            used_fact_ids=list(dict.fromkeys(fact_ids)),
        )
        if violations:
            details = "; ".join(f"{item.code}: {item.message}" for item in violations)
            raise ValueError(f"CV section {section_id} violates adaptation policy: {details}")

    result = CvDocumentDraft(
        document=draft,
        provenance=MappingProxyType(provenance),
        source_blocks=MappingProxyType(changed_source_blocks),
    )
    validate_cv_document(snapshot, result)
    return result


def validate_cv_document(
    snapshot: CandidateSnapshot,
    draft: CvDocumentDraft,
) -> None:
    source = snapshot.cv_source
    index = index_cv_source(source)
    section_provenance = {
        "projects": "projects.section",
        "skills": "skills.section",
    }
    allowed_provenance = set(index) | set(section_provenance.values())
    mapping = snapshot.cv_mapping
    if mapping is not None:
        allowed_provenance.update(
            f"source_block.{block.block_id}"
            for block in mapping.blocks
            if block.kind.value == "other"
        )
    unknown_provenance = sorted(set(draft.provenance) - allowed_provenance)
    if unknown_provenance:
        raise ValueError(f"Unknown CV provenance IDs: {unknown_provenance}")
    if set(draft.source_blocks) != {
        source_id for source_id in draft.provenance if source_id.startswith("source_block.")
    }:
        raise ValueError("Custom CV source block content does not match provenance")
    _require_distinct_structured_sections(draft.document)
    snapshot.require_evidence_ids(
        [
            evidence_id
            for source_id in draft.source_blocks
            for evidence_id in draft.provenance[source_id]
        ]
    )
    if mapping is not None:
        blocks_by_id = {block.block_id: block for block in mapping.blocks}
        for source_id, value in draft.source_blocks.items():
            block_id = source_id.removeprefix("source_block.")
            block = blocks_by_id.get(block_id)
            if block is None or block.kind.value != "other":
                raise ValueError(f"Unknown custom CV source block: {source_id}")
            if not value.strip():
                raise ValueError(f"Custom CV source block is blank: {source_id}")
            original = snapshot.cv_template_bytes[block.start_byte : block.end_byte].decode("utf-8")
            violations = validate_section_change(
                block.policy,
                original,
                value,
                used_fact_ids=list(draft.provenance[source_id]),
            )
            if violations:
                details = "; ".join(f"{item.code}: {item.message}" for item in violations)
                raise ValueError(
                    f"CV source block {source_id} violates adaptation policy: {details}"
                )
            snapshot.require_protected_claim_values(
                value,
                list(draft.provenance[source_id]),
            )
            snapshot.require_supported_claim_values(
                value,
                [source_id, *draft.provenance[source_id]],
            )

    facts_by_section: dict[str, list[str]] = {}
    replaced_sections: set[str] = set()
    for section_id, source_id in section_provenance.items():
        if source_id not in draft.provenance:
            continue
        fact_ids = list(draft.provenance[source_id])
        snapshot.require_evidence_ids(fact_ids)
        if section_id == "projects":
            for entry in draft.document.projects:
                for bullet in entry.bullets:
                    snapshot.require_supported_claim_values(bullet, fact_ids)
        facts_by_section[section_id] = fact_ids
        replaced_sections.add(section_id)
    for source_id, field_ref in index.items():
        if field_ref.section_id in replaced_sections:
            continue
        before = _field_value(source, field_ref)
        after = _field_value(draft.document, field_ref)
        if before == after:
            continue
        replacement_id = section_provenance.get(field_ref.section_id)
        if source_id not in draft.provenance and replacement_id not in draft.provenance:
            raise ValueError(f"Missing CV provenance for changed field: {source_id}")
        fact_ids = list(
            draft.provenance.get(
                source_id,
                draft.provenance.get(replacement_id or "", ()),
            )
        )
        snapshot.require_evidence_ids(fact_ids)
        if source_id == "summary.text" or ".bullet." in source_id:
            rendered_field = "\n".join(after) if isinstance(after, list) else after
            snapshot.require_supported_claim_values(rendered_field, fact_ids)
        facts_by_section.setdefault(field_ref.section_id, []).extend(fact_ids)

    cv_policy = snapshot.adaptation_policy.documents["cv"]
    for section_id, section_policy in cv_policy.sections.items():
        before = _section_text(source, section_id)
        after = _section_text(draft.document, section_id)
        if before == after:
            continue
        violations = validate_section_change(
            section_policy,
            before,
            after,
            used_fact_ids=list(dict.fromkeys(facts_by_section.get(section_id, []))),
        )
        if violations:
            details = "; ".join(f"{item.code}: {item.message}" for item in violations)
            raise ValueError(f"CV section {section_id} violates adaptation policy: {details}")
        snapshot.require_protected_claim_values(
            after,
            list(dict.fromkeys(facts_by_section.get(section_id, []))),
        )


def _require_distinct_structured_sections(document: CvSourceDocument) -> None:
    """Reject a canonical section copied into skills merely to fill the page."""
    language_items = _section_items(document.languages)
    if len(language_items) < 2:
        return
    for label, skills in document.skills.items():
        skill_items = {_normalized_section_item(item) for item in skills}
        overlap = language_items & skill_items
        if len(overlap) >= 2 and len(overlap) / len(language_items) >= 0.67:
            raise ValueError(f"CV skills duplicate the dedicated languages section: {label}")


def _without_dedicated_section_duplicates(
    groups: dict[str, list[str]],
    *,
    languages: str,
) -> dict[str, list[str]]:
    dedicated_items = _section_items(languages)
    if not dedicated_items:
        return groups
    filtered = {
        label: [item for item in items if _normalized_section_item(item) not in dedicated_items]
        for label, items in groups.items()
    }
    return {label: items for label, items in filtered.items() if items}


def _section_items(value: str) -> set[str]:
    return {
        normalized
        for item in re.split(r"[|,;]", value)
        if (normalized := _normalized_section_item(item))
    }


def _normalized_section_item(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in normalized
            if not unicodedata.combining(character)
        ).split()
    )


def changed_cv_fragments(
    snapshot: CandidateSnapshot,
    draft: CvDocumentDraft,
) -> dict[str, tuple[str, ...]]:
    """Return the semantic text that a rendered CV must visibly preserve."""
    validate_cv_document(snapshot, draft)
    index = index_cv_source(draft.document)
    fragments: dict[str, tuple[str, ...]] = {}
    for source_id in draft.provenance:
        if source_id.startswith("source_block."):
            fragments[source_id] = tuple(
                line for line in draft.source_blocks[source_id].splitlines() if line.strip()
            )
            continue
        if source_id == "projects.section":
            fragments[source_id] = tuple(
                value
                for entry in draft.document.projects
                for value in (entry.title, entry.dates, entry.stack, *entry.bullets)
                if value
            )
            continue
        if source_id == "skills.section":
            fragments[source_id] = tuple(
                value
                for label, items in draft.document.skills.items()
                for value in (label, *items)
                if value
            )
            continue
        field_ref = index[source_id]
        value = _field_value(draft.document, field_ref)
        fragments[source_id] = tuple(value) if isinstance(value, list) else (value,)
    return fragments


def _require_change_allowed(policy: SectionPolicy, field_ref: CvFieldRef) -> None:
    capabilities = policy.capabilities
    if not capabilities.rephrase:
        raise ValueError(f"CV source ID is locked: {field_ref.source_id}")
    if field_ref.value_kind == "items" and not capabilities.replace:
        raise ValueError(f"CV source ID cannot replace a list: {field_ref.source_id}")


def _can_recompose_structured_section(policy: SectionPolicy) -> bool:
    """An adaptable ordered section may select and reorder structured entries."""
    return policy.capabilities.reorder


def _fit_project_entries_to_source_shape(
    snapshot: CandidateSnapshot,
    entries: list[CvEntry],
) -> list[CvEntry]:
    block = _source_preserving_block(snapshot, "projects")
    if block is None or block.policy.capabilities.replace:
        return entries
    source_entries = snapshot.cv_source.projects
    if len(entries) != len(source_entries):
        raise ValueError(
            "source-preserving projects must keep the source entry count: "
            f"expected {len(source_entries)}, got {len(entries)}"
        )
    for source_entry, entry in zip(source_entries, entries, strict=True):
        target_count = len(source_entry.bullets)
        if len(entry.bullets) < target_count:
            raise ValueError(
                "source-preserving project has too few bullets for the LaTeX shape: "
                f"expected {target_count}, got {len(entry.bullets)}"
            )
        entry.bullets = _collapse_bullets(entry.bullets, target_count)
    return entries


def _require_skill_groups_fit_source_shape(
    snapshot: CandidateSnapshot,
    groups: dict[str, list[str]],
) -> None:
    block = _source_preserving_block(snapshot, "skills")
    if block is None or block.policy.capabilities.replace:
        return
    expected = len(snapshot.cv_source.skills)
    if len(groups) != expected:
        raise ValueError(
            "source-preserving skills must keep the source group count: "
            f"expected {expected}, got {len(groups)}"
        )


def _source_preserving_block(snapshot: CandidateSnapshot, section_id: str):
    mapping = snapshot.cv_mapping
    if mapping is None:
        return None
    return next(
        (block for block in mapping.blocks if block.kind.value == section_id),
        None,
    )


def _collapse_bullets(bullets: list[str], target_count: int) -> list[str]:
    if target_count == 0:
        if bullets:
            raise ValueError("source-preserving project cannot introduce bullet commands")
        return []
    if len(bullets) == target_count:
        return list(bullets)
    groups: list[list[str]] = [[] for _ in range(target_count)]
    for index, bullet in enumerate(bullets):
        group_index = min(index * target_count // len(bullets), target_count - 1)
        groups[group_index].append(bullet)
    return [" ".join(group) for group in groups]


def _apply_change(
    document: CvSourceDocument,
    field_ref: CvFieldRef,
    value: str | list[str],
) -> None:
    parts = field_ref.source_id.split(".")
    if field_ref.source_id == "headline.text":
        document.headline = _require_text(value)
        return
    if field_ref.source_id == "summary.text":
        document.summary = _require_text(value)
        return
    if field_ref.source_id == "languages.text":
        document.languages = _require_text(value)
        return
    if field_ref.source_id == "interests.text":
        document.interests = _require_text(value)
        return
    if parts[0] == "additional":
        document.additional_sections[int(parts[1])].content = _require_text(value)
        return
    if parts[0] == "skills":
        skill_index = int(parts[1])
        label, items = list(document.skills.items())[skill_index]
        if parts[2] == "items":
            document.skills[label] = _require_items(value)
            return
        new_label = _require_text(value)
        if new_label != label and new_label in document.skills:
            raise ValueError(f"duplicate skill section label: {new_label}")
        rebuilt = list(document.skills.items())
        rebuilt[skill_index] = (new_label, items)
        document.skills = dict(rebuilt)
        return

    entries = getattr(document, parts[0])
    entry = entries[int(parts[1])]
    field = parts[2]
    if field == "bullet":
        entry.bullets[int(parts[3])] = _require_text(value)
        return
    setattr(entry, field, _require_text(value))


def _field_value(document: CvSourceDocument, field_ref: CvFieldRef) -> str | list[str]:
    parts = field_ref.source_id.split(".")
    if field_ref.source_id == "headline.text":
        return document.headline
    if field_ref.source_id == "summary.text":
        return document.summary
    if field_ref.source_id == "languages.text":
        return document.languages
    if field_ref.source_id == "interests.text":
        return document.interests
    if parts[0] == "additional":
        return document.additional_sections[int(parts[1])].content
    if parts[0] == "skills":
        label, items = list(document.skills.items())[int(parts[1])]
        return list(items) if parts[2] == "items" else label
    entry = getattr(document, parts[0])[int(parts[1])]
    if parts[2] == "bullet":
        return entry.bullets[int(parts[3])]
    return str(getattr(entry, parts[2]) or "")


def _section_text(document: CvSourceDocument, section_id: str) -> str:
    if section_id == "identity":
        return f"{document.name}\n{document.contact_line}"
    if section_id == "summary":
        return f"{document.headline}\n{document.summary}"
    if section_id == "skills":
        return "\n".join(f"{label}: {', '.join(items)}" for label, items in document.skills.items())
    if section_id in {"languages", "interests"}:
        return str(getattr(document, section_id))
    if section_id.startswith("additional_"):
        try:
            section = document.additional_sections[int(section_id.removeprefix("additional_"))]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Unknown additional CV section: {section_id}") from exc
        return f"{section.label}\n{section.content}"
    entries = getattr(document, section_id)
    lines: list[str] = []
    for entry in entries:
        lines.extend(
            item for item in (entry.title, entry.dates, entry.stack, *entry.bullets) if item
        )
    return "\n".join(lines)


def _require_text(value: str | list[str]) -> str:
    if not isinstance(value, str):
        raise ValueError("CV text field requires a string replacement")
    return value


def _require_items(value: str | list[str]) -> list[str]:
    if isinstance(value, str):
        raise ValueError("CV item field requires a list replacement")
    return list(value)
