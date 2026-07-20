from __future__ import annotations

from pathlib import Path

from jobauto.adaptation_policy import AdaptationPolicy
from jobauto.cv_source import CvEntry, CvSourceDocument
from jobauto.latex_utils import latex_escape

BODY_MARKER = "%%JOBAUTO_BODY%%"


def render_profile_cv_tex(
    template_path: Path,
    document: CvSourceDocument,
    policy: AdaptationPolicy,
    *,
    locale: str,
) -> str:
    template = template_path.read_text(encoding="utf-8")
    return render_profile_cv_tex_source(template, document, policy, locale=locale)


def render_profile_cv_tex_source(
    template: str,
    document: CvSourceDocument,
    policy: AdaptationPolicy,
    *,
    locale: str,
) -> str:
    if BODY_MARKER not in template:
        raise ValueError(f"CV template must contain {BODY_MARKER}")
    try:
        cv_policy = policy.documents["cv"]
    except KeyError as exc:
        raise ValueError("Adaptation policy has no cv document") from exc
    renderers = {
        "identity": lambda: _render_identity(document),
        "summary": lambda: _render_text_section(_label("summary", locale), document.summary),
        "experience": lambda: _render_entries(
            _label("experience", locale), document.experience, kind="experience"
        ),
        "projects": lambda: _render_entries(
            _label("projects", locale), document.projects, kind="projects"
        ),
        "skills": lambda: _render_skills(_label("skills", locale), document.skills),
        "education": lambda: _render_entries(
            _label("education", locale), document.education, kind="education"
        ),
        "languages": lambda: _render_text_section(_label("languages", locale), document.languages),
        "interests": lambda: _render_text_section(_label("interests", locale), document.interests),
    }
    renderers.update(
        {
            f"additional_{index}": lambda section=section: _render_text_section(
                section.label,
                section.content,
            )
            for index, section in enumerate(document.additional_sections)
        }
    )
    sections: list[str] = []
    for section_id in cv_policy.section_order:
        if section_id not in renderers:
            raise ValueError(f"Unsupported rendered CV section: {section_id}")
        rendered = renderers[section_id]()
        section_policy = cv_policy.sections[section_id]
        if rendered:
            sections.append(rendered)
        elif section_policy.required:
            raise ValueError(f"Required CV section is empty: {section_id}")
    return template.replace(BODY_MARKER, "\n\n".join(sections))


def _render_identity(document: CvSourceDocument) -> str:
    return "\n".join(
        [
            r"\begin{center}",
            rf"{{\Large \textbf{{{latex_escape(document.name)}}}}}\\[3pt]",
            rf"\textbf{{{latex_escape(document.headline)}}}\\[3pt]",
            latex_escape(document.contact_line),
            r"\end{center}",
        ]
    )


def _render_text_section(label: str, value: str) -> str:
    if not value.strip():
        return ""
    return rf"\cvsection{{{latex_escape(label)}}}" + "\n" + latex_escape(value)


def _render_entries(label: str, entries: list[CvEntry], *, kind: str) -> str:
    if not entries:
        return ""
    rendered = [rf"\cvsection{{{latex_escape(label)}}}"]
    for entry in entries:
        suffix = entry.stack if kind == "projects" else entry.dates
        heading = rf"\textbf{{{latex_escape(entry.title)}}}"
        if suffix:
            separator = r" \;|\; \textit" if kind == "projects" else r" \hfill \textit"
            heading += separator + "{" + latex_escape(suffix) + "}"
        rendered.append(heading)
        if entry.bullets:
            rendered.append(r"\begin{itemize}")
            rendered.extend(rf"\item {latex_escape(bullet)}" for bullet in entry.bullets)
            rendered.append(r"\end{itemize}")
    return "\n".join(rendered)


def _render_skills(label: str, skills: dict[str, list[str]]) -> str:
    if not skills:
        return ""
    lines = [rf"\cvsection{{{latex_escape(label)}}}"]
    items = list(skills.items())
    for index, (group, values) in enumerate(items):
        suffix = r"\\[2pt]" if index < len(items) - 1 else ""
        lines.append(
            rf"\textbf{{{latex_escape(group)}}}: "
            + ", ".join(latex_escape(value) for value in values)
            + suffix
        )
    return "\n".join(lines)


def _label(section_id: str, locale: str) -> str:
    french = locale.casefold().startswith("fr")
    labels = {
        "summary": ("Résumé", "Profile"),
        "experience": ("Expérience", "Experience"),
        "projects": ("Projets", "Projects"),
        "skills": ("Compétences", "Skills"),
        "education": ("Formation", "Education"),
        "languages": ("Langues", "Languages"),
        "interests": ("Centres d'intérêt", "Interests"),
    }
    return labels[section_id][0 if french else 1]
