from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jobauto.cv_source import CvEntry, CvSourceDocument
from jobauto.models import ApplicationBrief


class CvChangeItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str
    label: str
    before: str
    after: str
    rationale: str


class CvChangeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    change_count: int = Field(ge=0)
    changed_sections: list[str]
    items: list[CvChangeItem]


def build_cv_change_summary(
    original: CvSourceDocument,
    run_dir: Path,
) -> CvChangeSummary | None:
    """Compare the source CV with the final agent-approved CV for a run."""
    adapted = _load_final_cv(run_dir)
    if adapted is None:
        return None
    brief = _load_brief(run_dir)
    return compare_cv_documents(original, adapted, brief=brief)


def compare_cv_documents(
    original: CvSourceDocument,
    adapted: CvSourceDocument,
    *,
    brief: ApplicationBrief | None = None,
) -> CvChangeSummary:
    changes: list[CvChangeItem] = []

    _append_text_change(
        changes, "headline", "Positioning", original.headline, adapted.headline, brief
    )
    _append_text_change(
        changes, "summary", "Profile summary", original.summary, adapted.summary, brief
    )

    for index in range(max(len(original.experience), len(adapted.experience))):
        before = original.experience[index] if index < len(original.experience) else None
        after = adapted.experience[index] if index < len(adapted.experience) else None
        if before == after:
            continue
        label = (
            (after or before).title if (after or before) is not None else f"Experience {index + 1}"
        )
        changes.append(
            CvChangeItem(
                section="experience",
                label=label,
                before=_format_entry(before),
                after=_format_entry(after),
                rationale=_rationale("experience", brief),
            )
        )

    _append_group_change(
        changes,
        "projects",
        "Project selection",
        original.projects,
        adapted.projects,
        brief,
    )
    _append_group_change(
        changes,
        "skills",
        "Visible competencies",
        original.skills,
        adapted.skills,
        brief,
    )
    _append_group_change(
        changes,
        "education",
        "Education",
        original.education,
        adapted.education,
        brief,
    )
    _append_group_change(
        changes,
        "additional_sections",
        "Additional sections",
        original.additional_sections,
        adapted.additional_sections,
        brief,
    )
    _append_text_change(
        changes,
        "languages",
        "Languages",
        original.languages,
        adapted.languages,
        brief,
    )
    _append_text_change(
        changes,
        "interests",
        "Interests",
        original.interests,
        adapted.interests,
        brief,
    )

    sections = list(dict.fromkeys(item.section for item in changes))
    return CvChangeSummary(
        change_count=len(changes),
        changed_sections=sections,
        items=changes,
    )


def _append_text_change(
    changes: list[CvChangeItem],
    section: str,
    label: str,
    before: str,
    after: str,
    brief: ApplicationBrief | None,
) -> None:
    if before.strip() == after.strip():
        return
    changes.append(
        CvChangeItem(
            section=section,
            label=label,
            before=before.strip() or "Not present",
            after=after.strip() or "Removed",
            rationale=_rationale(section, brief),
        )
    )


def _append_group_change(
    changes: list[CvChangeItem],
    section: str,
    label: str,
    before: object,
    after: object,
    brief: ApplicationBrief | None,
) -> None:
    if before == after:
        return
    changes.append(
        CvChangeItem(
            section=section,
            label=label,
            before=_format_group(before),
            after=_format_group(after),
            rationale=_rationale(section, brief),
        )
    )


def _format_group(value: object) -> str:
    if isinstance(value, dict):
        if not value:
            return "Not present"
        return "\n".join(f"{label}: {', '.join(items)}" for label, items in value.items())
    if isinstance(value, list):
        if not value:
            return "Not present"
        return "\n\n".join(
            _format_entry(item) if isinstance(item, CvEntry) else str(item) for item in value
        )
    return str(value)


def _format_entry(entry: CvEntry | None) -> str:
    if entry is None:
        return "Not present"
    suffix = entry.stack or entry.dates
    heading = f"{entry.title} | {suffix}" if suffix else entry.title
    return "\n".join([heading, *(f"• {bullet}" for bullet in entry.bullets)])


def _rationale(section: str, brief: ApplicationBrief | None) -> str:
    if brief is None:
        return "The final version was adjusted to improve relevance while preserving candidate evidence."
    if section == "headline":
        return f"Makes the target role ({brief.normalized_role}) immediately visible."
    if section == "summary":
        return brief.cv_angle
    if section == "projects":
        return brief.project_plan.rationale
    if section == "skills":
        return brief.skill_plan.rationale
    if section == "experience":
        return brief.cv_angle
    return "Kept only where the application strategy required a supported change."


def _load_final_cv(run_dir: Path) -> CvSourceDocument | None:
    packages = sorted(
        run_dir.glob("candidate-package-*.json"),
        key=lambda path: (_numeric_suffix(path), path.stat().st_mtime_ns),
    )
    for path in reversed(packages):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return CvSourceDocument.model_validate(payload["cv_document"])
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None


def _load_brief(run_dir: Path) -> ApplicationBrief | None:
    try:
        return ApplicationBrief.model_validate_json(
            (run_dir / "application-brief.json").read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError):
        return None


def _numeric_suffix(path: Path) -> int:
    match = re.search(r"-(\d+)$", path.stem)
    return int(match.group(1)) if match else -1
