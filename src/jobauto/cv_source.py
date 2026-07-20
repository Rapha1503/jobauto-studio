from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CvEntry(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    dates: str | None = Field(default=None, max_length=100)
    stack: str | None = Field(default=None, max_length=300)
    bullets: list[str] = Field(default_factory=list)


class CvAdditionalSection(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)


class CvSourceDocument(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    headline: str = Field(min_length=1, max_length=240)
    contact_line: str = Field(min_length=1, max_length=500)
    summary: str = ""
    experience: list[CvEntry] = Field(default_factory=list)
    projects: list[CvEntry] = Field(default_factory=list)
    skills: dict[str, list[str]] = Field(default_factory=dict)
    education: list[CvEntry] = Field(default_factory=list)
    additional_sections: list[CvAdditionalSection] = Field(default_factory=list)
    languages: str = ""
    interests: str = ""

    @classmethod
    def parse(cls, text: str) -> CvSourceDocument:
        lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
        non_blank = [index for index, line in enumerate(lines) if line.strip()]
        if len(non_blank) < 3 or not lines[non_blank[0]].startswith("# "):
            raise ValueError(
                "CV source must start with '# <candidate name>', headline and contact line"
            )
        name_index, headline_index, contact_index = non_blank[:3]
        document = cls(
            name=lines[name_index][2:].strip(),
            headline=lines[headline_index].strip(),
            contact_line=lines[contact_index].strip(),
        )
        section: str | None = None
        active_entry: CvEntry | None = None
        active_additional: CvAdditionalSection | None = None
        summary_lines: list[str] = []
        language_lines: list[str] = []
        interest_lines: list[str] = []
        for raw in lines[contact_index + 1 :]:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("## "):
                label = line[3:].strip()
                section = _section_id(label)
                active_additional = None
                if section is None:
                    active_additional = CvAdditionalSection(label=label, content=" ")
                    document.additional_sections.append(active_additional)
                    section = "additional"
                active_entry = None
                continue
            if line.startswith("### "):
                if section == "additional" and active_additional is not None:
                    active_additional.content = _append_content(
                        active_additional.content,
                        line[4:].strip(),
                    )
                    continue
                if section not in {"experience", "projects", "education"}:
                    raise ValueError(f"Entry heading is not allowed in section {section!r}")
                active_entry = _parse_entry(line[4:], section)
                getattr(document, section).append(active_entry)
                continue
            if line.startswith("- "):
                if section == "additional" and active_additional is not None:
                    active_additional.content = _append_content(
                        active_additional.content,
                        f"- {line[2:].strip()}",
                    )
                    continue
                if active_entry is None:
                    raise ValueError(
                        "Bullet found outside an experience, project or education entry"
                    )
                active_entry.bullets.append(line[2:].strip())
                continue
            if section == "summary":
                summary_lines.append(line)
            elif section == "skills":
                if ":" not in line:
                    raise ValueError("Skill lines must use '<category>: item, item'")
                label, values = line.split(":", 1)
                document.skills[label.strip()] = [
                    item.strip() for item in values.split(",") if item.strip()
                ]
            elif section == "education" and active_entry is not None:
                active_entry.bullets.append(line)
            elif section == "languages":
                language_lines.append(line)
            elif section == "interests":
                interest_lines.append(line)
            elif section == "additional" and active_additional is not None:
                active_additional.content = _append_content(active_additional.content, line)
            else:
                raise ValueError(f"Unexpected CV source line: {line}")
        document.summary = " ".join(summary_lines)
        document.languages = " ".join(language_lines)
        document.interests = " ".join(interest_lines)
        return document


def _section_id(label: str) -> str | None:
    normalized = label.strip().casefold()
    aliases = {
        "summary": "summary",
        "profile": "summary",
        "resume": "summary",
        "résumé": "summary",
        "experience": "experience",
        "expérience": "experience",
        "projects": "projects",
        "projets": "projects",
        "skills": "skills",
        "compétences": "skills",
        "competences": "skills",
        "education": "education",
        "formation": "education",
        "languages": "languages",
        "langues": "languages",
        "interests": "interests",
        "centres d'intérêt": "interests",
    }
    return aliases.get(normalized)


def _append_content(current: str, line: str) -> str:
    lines = current.splitlines() if current.strip() else []
    lines.append(line)
    return "\n".join(lines)


def _parse_entry(
    heading: str,
    section: Literal["experience", "projects", "education"] | str,
) -> CvEntry:
    title, separator, suffix = heading.partition("|")
    if section == "projects":
        return CvEntry(title=title.strip(), stack=suffix.strip() if separator else None)
    return CvEntry(title=title.strip(), dates=suffix.strip() if separator else None)
