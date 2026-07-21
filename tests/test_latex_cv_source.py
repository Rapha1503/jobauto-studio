from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from jobauto.adaptation_policy import STUDIO_ADAPTATION_PRESETS, FidelityLevel
from jobauto.build import compile_latex
from jobauto.latex_cv_source import (
    TexBlockCorrection,
    TexBlockKind,
    analyze_latex_cv,
    apply_block_replacements,
    corrected_mapping,
)

SOURCE = rb"""\documentclass[10pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\newcommand{\cvsection}[1]{\section*{#1}}
\begin{document}
\begin{center}
{\Large Alex Morgan}
\end{center}
\cvsection{Profile}
Data engineer building reliable pipelines.
\cvsection{Experience}
\textbf{GridCo} \hfill 2024--2026
\begin{itemize}
\item Built SQL pipelines. % kept comment
\end{itemize}
\cvsection{Technical Skills}
Python, SQL, BigQuery
\end{document}
"""


def test_analyze_latex_cv_preserves_source_identity_and_detects_semantic_blocks() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")

    assert mapping.source_sha256 == hashlib.sha256(SOURCE).hexdigest()
    assert [block.kind for block in mapping.blocks] == [
        TexBlockKind.IDENTITY,
        TexBlockKind.SUMMARY,
        TexBlockKind.EXPERIENCE,
        TexBlockKind.SKILLS,
    ]
    assert mapping.blocks[1].detector == "semantic-command:cvsection"
    assert mapping.blocks[0].policy.fidelity is STUDIO_ADAPTATION_PRESETS["balanced"]["identity"]
    assert mapping.blocks[1].policy.fidelity is STUDIO_ADAPTATION_PRESETS["balanced"]["summary"]
    assert (
        mapping.preamble_sha256 == hashlib.sha256(SOURCE[: mapping.preamble_end_byte]).hexdigest()
    )


def test_unknown_named_sections_are_preserved_as_independent_other_blocks() -> None:
    source = SOURCE.replace(
        b"\\end{document}",
        b"\\cvsection{Certifications}\nISO 13485 Lead Auditor\n"
        b"\\cvsection{Publications}\nTwo peer-reviewed articles\n"
        b"\\end{document}",
    )

    mapping = analyze_latex_cv(source, filename="specialist.tex")
    custom = [block for block in mapping.blocks if block.kind is TexBlockKind.OTHER]

    assert [block.block_id for block in custom] == ["other", "other-2"]
    assert [block.label for block in custom] == ["Certifications", "Publications"]
    assert custom[0].start_byte < custom[0].end_byte <= custom[1].start_byte


def test_contact_section_is_recognized_as_candidate_identity() -> None:
    source = (
        b"\\documentclass{article}\n"
        b"\\begin{document}\n"
        b"\\section{Contact}\n"
        b"Morgan Lee | morgan.lee@example.test | London\n"
        b"\\section{Experience}\n"
        b"Exhibition coordination and supplier management.\n"
        b"\\end{document}\n"
    )

    mapping = analyze_latex_cv(source, filename="producer.tex")

    assert mapping.blocks[0].block_id == "identity"
    assert mapping.blocks[0].kind is TexBlockKind.IDENTITY
    assert mapping.blocks[1].kind is TexBlockKind.EXPERIENCE


def test_custom_section_macro_preserves_unknown_cv_category() -> None:
    source = SOURCE.replace(
        b"\\end{document}",
        b"\\resumeSection{Professional Memberships}\nRAPS member\n\\end{document}",
    )

    mapping = analyze_latex_cv(source, filename="specialist.tex")
    custom = [block for block in mapping.blocks if block.kind is TexBlockKind.OTHER]

    assert len(custom) == 1
    assert custom[0].label == "Professional Memberships"
    assert custom[0].detector == "semantic-command:resumeSection"


def test_section_title_macro_preserves_unknown_cv_category() -> None:
    source = (
        b"\\documentclass{article}\n"
        b"\\begin{document}\n"
        b"Jane Doe\n"
        b"\\sectionTitle{Publications}\n"
        b"A peer-reviewed article\n"
        b"\\end{document}\n"
    )

    mapping = analyze_latex_cv(source, filename="cv.tex")
    custom = [block for block in mapping.blocks if block.kind is TexBlockKind.OTHER]

    assert len(custom) == 1
    assert custom[0].label == "Publications"
    assert custom[0].detector == "semantic-command:sectionTitle"


def test_declared_display_macro_preserves_arbitrary_cv_categories() -> None:
    source = rb"""\documentclass{article}
\newcommand{\rubrique}[1]{\par\large\bfseries #1\par\hrule}
\begin{document}
Maya Laurent
\rubrique{Publications}
Two peer-reviewed articles
\rubrique{Professional Memberships}
ICOM emerging professionals network
\end{document}
"""

    mapping = analyze_latex_cv(source, filename="researcher.tex")
    custom = [block for block in mapping.blocks if block.kind is TexBlockKind.OTHER]

    assert [block.label for block in custom] == [
        "Publications",
        "Professional Memberships",
    ]
    assert all(block.detector == "semantic-command:rubrique" for block in custom)


def test_block_replacement_changes_only_selected_span_and_keeps_preamble_exact() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")
    summary = next(block for block in mapping.blocks if block.kind is TexBlockKind.SUMMARY)
    replacement = "\\cvsection{Profile}\nData and AI engineer.\n"

    patched = apply_block_replacements(SOURCE, mapping, {summary.block_id: replacement})

    assert patched[: mapping.preamble_end_byte] == SOURCE[: mapping.preamble_end_byte]
    assert b"Data and AI engineer" in patched
    assert b"Built SQL pipelines" in patched
    assert b"Python, SQL, BigQuery" in patched


def test_multiple_block_replacements_are_applied_from_the_end_of_the_source() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")
    summary = next(block for block in mapping.blocks if block.kind is TexBlockKind.SUMMARY)
    skills = next(block for block in mapping.blocks if block.kind is TexBlockKind.SKILLS)

    patched = apply_block_replacements(
        SOURCE,
        mapping,
        {
            summary.block_id: "\\cvsection{Profile}\nAI engineer.\n",
            skills.block_id: "\\cvsection{Technical Skills}\nPython, SQL, Docker\n",
        },
    )

    assert b"AI engineer" in patched
    assert b"Python, SQL, Docker" in patched
    assert b"Built SQL pipelines" in patched


def test_block_replacement_rejects_stale_source() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")

    with pytest.raises(ValueError, match="changed after block detection"):
        apply_block_replacements(SOURCE + b"% changed", mapping, {})


def test_user_correction_rebuilds_exact_non_overlapping_byte_ranges() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")
    corrected = corrected_mapping(
        SOURCE,
        mapping,
        [
            TexBlockCorrection(
                block_id="profile-custom",
                label="Profile",
                kind=TexBlockKind.SUMMARY,
                start_line=8,
                end_line=9,
                fidelity=FidelityLevel.ADAPTABLE,
                target_lines=3,
            ),
            TexBlockCorrection(
                block_id="experience-custom",
                label="Experience",
                kind=TexBlockKind.EXPERIENCE,
                start_line=10,
                end_line=15,
                fidelity=FidelityLevel.VERY_FAITHFUL,
            ),
        ],
    )

    assert [block.detector for block in corrected.blocks] == [
        "user-confirmed",
        "user-confirmed",
    ]
    assert corrected.blocks[0].start_byte < corrected.blocks[0].end_byte
    assert corrected.blocks[0].policy.target_lines == 3


def test_mapping_rejects_overlapping_user_ranges() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")

    with pytest.raises(ValueError, match="overlap"):
        corrected_mapping(
            SOURCE,
            mapping,
            [
                TexBlockCorrection(
                    block_id="summary-one",
                    label="Profile",
                    kind=TexBlockKind.SUMMARY,
                    start_line=8,
                    end_line=12,
                    fidelity=FidelityLevel.ADAPTABLE,
                ),
                TexBlockCorrection(
                    block_id="experience-one",
                    label="Experience",
                    kind=TexBlockKind.EXPERIENCE,
                    start_line=10,
                    end_line=15,
                    fidelity=FidelityLevel.VERY_FAITHFUL,
                ),
            ],
        )


def test_mapping_rejects_duplicate_ids_and_inconsistent_byte_ranges() -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")
    duplicate = mapping.model_dump(mode="python")
    duplicate["blocks"][1]["block_id"] = duplicate["blocks"][0]["block_id"]
    with pytest.raises(ValueError, match="IDs must be unique"):
        type(mapping).model_validate(duplicate)

    inconsistent = mapping.model_copy(
        update={
            "blocks": [
                mapping.blocks[0].model_copy(
                    update={"start_byte": mapping.blocks[0].start_byte + 1}
                ),
                *mapping.blocks[1:],
            ]
        }
    )
    with pytest.raises(ValueError, match="start byte does not match"):
        apply_block_replacements(SOURCE, inconsistent, {})


@pytest.mark.parametrize("filename", ["candidate.txt", "candidate.pdf"])
def test_import_requires_a_tex_filename(filename: str) -> None:
    with pytest.raises(ValueError, match="end with .tex"):
        analyze_latex_cv(SOURCE, filename=filename)


def test_mapping_load_migrates_pre_policy_sidecar(tmp_path: Path) -> None:
    mapping = analyze_latex_cv(SOURCE, filename="candidate.tex")
    payload = mapping.model_dump(mode="json")
    for block in payload["blocks"]:
        block.pop("policy")
    path = tmp_path / "cv_map.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = type(mapping).load(path)

    assert migrated.blocks[0].policy.fidelity is STUDIO_ADAPTATION_PRESETS["balanced"]["identity"]
    assert migrated.blocks[1].policy.fidelity is STUDIO_ADAPTATION_PRESETS["balanced"]["summary"]


def test_import_rejects_unreasonably_large_tex_source() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        analyze_latex_cv(b"x" * 2_000_001, filename="candidate.tex")


def test_unicode_patch_survives_real_latex_and_pdf_render(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    mapping = analyze_latex_cv(source, filename=fixture.name)
    summary = next(block for block in mapping.blocks if block.kind is TexBlockKind.SUMMARY)
    patched = apply_block_replacements(
        source,
        mapping,
        {
            summary.block_id: (
                "\\cvsection{Résumé}\n"
                "Ingénieure IA/Data expérimentée en systèmes énergétiques. "
                "Recherche un poste adapté à partir de septembre 2026.\n"
            )
        },
    )
    tex_path = tmp_path / "unicode_cv.tex"
    tex_path.write_bytes(patched)

    pdf_path, _ = compile_latex(tex_path, tmp_path / "build")
    extracted = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)

    assert "Résumé" in extracted
    assert "Ingénieure IA/Data expérimentée" in extracted
    assert "adapté à partir" in extracted
    assert len(PdfReader(str(pdf_path)).pages) == 1


def test_contradictory_engineering_fixture_is_a_real_one_page_pdf(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_engineering.tex"
    tex_path = tmp_path / fixture.name
    tex_path.write_bytes(fixture.read_bytes())

    pdf_path, _ = compile_latex(tex_path, tmp_path / "engineering-build")
    reader = PdfReader(str(pdf_path))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert len(reader.pages) == 1
    assert "NOAH BENNETT" in extracted
    assert "ANSYS" in extracted
    assert "Camille" not in extracted
