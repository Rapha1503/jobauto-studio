import shutil
from pathlib import Path

import pytest

from jobauto.adaptation_policy import AdaptationPolicy
from jobauto.build import compile_latex
from jobauto.candidate_profile import CandidateProfile
from jobauto.cv_source import CvSourceDocument
from jobauto.generic_cv_renderer import render_profile_cv_tex
from jobauto.public_validation import extract_pdf_text, pdf_page_count

SOURCE = """# Alex Morgan
Data Engineer | Python, SQL, Cloud | Toulouse
Email: alex.morgan@example.test | Phone: +33 1 00 00 00 00 | LinkedIn: example.test/alex

## Summary
Data engineer focused on reliable analytics pipelines and useful data products.

## Experience
### Northwind Energy - Data Engineer | 2024 - 2026
- Built Python and SQL ingestion pipelines for operational datasets.
- Worked with business teams to define quality controls and reporting needs.

## Projects
### Energy demand forecasting | Python, pandas, scikit-learn, MLflow
- Built a time-series training and monitoring workflow for electricity-demand data.

## Skills
Data Engineering & Cloud: Python, SQL, ETL/ELT, BigQuery
Machine Learning: pandas, scikit-learn, MLflow

## Education
### Engineering School - Data major | 2021 - 2026
Statistics, machine learning and distributed systems.

## Languages
French native | English C1

## Interests
Energy systems | Open source
"""


def _policy(path: Path) -> AdaptationPolicy:
    path.write_text(
        """policy_id: test
documents:
  cv:
    section_order: [identity, summary, experience, projects, skills, education, languages, interests]
    sections:
      identity: {fidelity: locked}
      summary: {fidelity: adaptable}
      experience: {fidelity: very_faithful}
      projects: {fidelity: highly_adaptable}
      skills: {fidelity: replaceable}
      education: {fidelity: locked}
      languages: {fidelity: locked}
      interests: {fidelity: locked, required: false}
""",
        encoding="utf-8",
    )
    return AdaptationPolicy.load(path)


def test_markdown_cv_parser_extracts_structured_sections() -> None:
    document = CvSourceDocument.parse(SOURCE)

    assert document.name == "Alex Morgan"
    assert document.headline.startswith("Data Engineer")
    assert document.experience[0].dates == "2024 - 2026"
    assert len(document.experience[0].bullets) == 2
    assert document.projects[0].stack == "Python, pandas, scikit-learn, MLflow"
    assert document.skills["Data Engineering & Cloud"] == ["Python", "SQL", "ETL/ELT", "BigQuery"]
    assert document.education[0].title == "Engineering School - Data major"
    assert document.languages == "French native | English C1"


def test_markdown_cv_parser_preserves_candidate_named_sections() -> None:
    document = CvSourceDocument.parse(
        SOURCE
        + "\n## Publications and certifications\n"
        + "- Two peer-reviewed articles\n"
        + "ISO 13485 internal auditor training\n"
    )

    assert document.additional_sections[0].label == "Publications and certifications"
    assert document.additional_sections[0].content == (
        "- Two peer-reviewed articles\nISO 13485 internal auditor training"
    )


def test_renderer_uses_profile_content_and_policy_order(tmp_path: Path) -> None:
    template = tmp_path / "template.tex"
    template.write_text(
        "\\documentclass[10pt,a4paper]{article}\n"
        "\\newcommand{\\cvsection}[1]{\\section*{#1}}\n"
        "\\begin{document}\n%%JOBAUTO_BODY%%\n\\end{document}\n",
        encoding="utf-8",
    )
    document = CvSourceDocument.parse(SOURCE)

    tex = render_profile_cv_tex(template, document, _policy(tmp_path / "policy.yaml"), locale="en")

    assert "Alex Morgan" in tex
    assert "Northwind Energy" in tex
    assert "Energy demand forecasting" in tex
    assert tex.index("Profile") < tex.index("Experience") < tex.index("Projects")
    assert "PrivateCandidate" not in tex
    assert "PrivateEmployer" not in tex


def test_renderer_rejects_template_without_body_marker(tmp_path: Path) -> None:
    template = tmp_path / "template.tex"
    template.write_text("\\begin{document}empty\\end{document}", encoding="utf-8")

    with pytest.raises(ValueError, match="JOBAUTO_BODY"):
        render_profile_cv_tex(
            template,
            CvSourceDocument.parse(SOURCE),
            _policy(tmp_path / "policy.yaml"),
            locale="en",
        )


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex is not installed")
def test_example_profile_renders_a_real_one_page_pdf(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    profile = CandidateProfile.load(
        project_root / "config" / "profiles" / "example" / "profile.yaml"
    )
    source = CvSourceDocument.parse(profile.cv_source_path.read_text(encoding="utf-8"))
    policy = AdaptationPolicy.load(profile.adaptation_policy_path)
    tex = render_profile_cv_tex(profile.cv_model_path, source, policy, locale=profile.locale)
    tex_path = tmp_path / "example_cv.tex"
    tex_path.write_text(tex, encoding="utf-8")

    pdf_path, _ = compile_latex(tex_path, tmp_path / "build")
    pdf_text = extract_pdf_text(pdf_path)

    assert pdf_page_count(pdf_path) == 1
    assert "Alex Morgan" in pdf_text
    assert "PrivateCandidate" not in pdf_text
    assert "PrivateEmployer" not in pdf_text
