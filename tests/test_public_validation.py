from pathlib import Path

from jobauto.build import compile_latex
from jobauto.public_validation import (
    cv_layout_requires_density_review,
    pdf_layout_metrics,
)


def _render(tmp_path: Path, body: str, stem: str) -> Path:
    source = tmp_path / f"{stem}.tex"
    source.write_text(
        "\\documentclass[a4paper]{article}\n"
        "\\usepackage[margin=2cm]{geometry}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n"
        f"{body}\n"
        "\\end{document}\n",
        encoding="utf-8",
    )
    pdf, _log = compile_latex(source, tmp_path / f"{stem}-build")
    return pdf


def test_layout_metrics_detect_a_large_internal_gap_hidden_by_full_page_coverage(
    tmp_path: Path,
) -> None:
    sparse = _render(
        tmp_path,
        "Top evidence\\par\\vfill Bottom evidence",
        "sparse",
    )
    dense = _render(
        tmp_path,
        "\\section*{Experience}\n"
        "Evidence line one.\\par Evidence line two.\\par Evidence line three.\n"
        "\\section*{Education}\n"
        "Degree and relevant coursework.\\par Certifications and training.",
        "dense",
    )

    sparse_metrics = pdf_layout_metrics(sparse)
    dense_metrics = pdf_layout_metrics(dense)

    assert sparse_metrics["vertical_coverage_ratio"] > 0.7
    assert sparse_metrics["largest_internal_gap_ratio"] > 0.6
    assert dense_metrics["largest_internal_gap_ratio"] < 0.1
    assert cv_layout_requires_density_review(
        sparse_metrics,
        at_layout_ceiling=False,
    )
    assert not cv_layout_requires_density_review(
        dense_metrics,
        at_layout_ceiling=False,
    )
    assert cv_layout_requires_density_review(dense_metrics, at_layout_ceiling=True)


def test_layout_density_review_flags_visible_underfill_only_at_layout_ceiling() -> None:
    underfilled = {
        "vertical_coverage_ratio": 0.80,
        "largest_internal_gap_ratio": 0.05,
    }
    filled = {
        "vertical_coverage_ratio": 0.84,
        "largest_internal_gap_ratio": 0.05,
    }

    assert cv_layout_requires_density_review(underfilled, at_layout_ceiling=True)
    assert not cv_layout_requires_density_review(underfilled, at_layout_ceiling=False)
    assert not cv_layout_requires_density_review(filled, at_layout_ceiling=True)
