from __future__ import annotations

import hashlib
from pathlib import Path
from types import MappingProxyType

import pytest

from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.document_patch import CvDocumentDraft
from jobauto.document_renderer import (
    DocumentRenderer,
    _normalize_matching_text,
    _semantic_fragment_is_present,
)
from jobauto.models import CandidateLetterDraft


def _snapshot():
    project_root = Path(__file__).resolve().parents[1]
    return CandidateProfileRepository(project_root / "config" / "profiles").load_snapshot(
        project_root / "config" / "profiles" / "example" / "profile.yaml"
    )


def _letter() -> CandidateLetterDraft:
    return CandidateLetterDraft(
        greeting="Dear hiring team,",
        paragraphs=[
            "I am applying for the Data Engineer role because its focus on reliable operational data products matches my experience building analytics pipelines.",
            "I would bring a practical combination of Python, SQL and data-quality work, with attention to maintainable delivery and useful outcomes for business teams.",
        ],
        closing="Kind regards,\nAlex Morgan",
        used_fact_ids=["identity.current"],
    )


def test_renderer_produces_exact_inspectable_one_page_documents(tmp_path: Path) -> None:
    snapshot = _snapshot()
    renderer = DocumentRenderer()
    cv_draft = CvDocumentDraft(
        document=snapshot.cv_source,
        provenance=MappingProxyType({}),
    )

    cv = renderer.render_cv(snapshot, cv_draft, tmp_path)
    letter = renderer.render_letter(snapshot, _letter(), tmp_path)

    for rendered in (cv, letter):
        assert rendered.source_path.is_file()
        assert rendered.pdf_path.is_file()
        assert rendered.page_count == 1
        assert rendered.pdf_sha256 == hashlib.sha256(rendered.pdf_path.read_bytes()).hexdigest()
        assert (
            rendered.extracted_text_sha256
            == hashlib.sha256(rendered.extracted_text.encode("utf-8")).hexdigest()
        )
        assert rendered.layout_metrics["pages"] == 1
        assert rendered.layout_metrics["vertical_coverage_ratio"] is not None
        assert "Alex Morgan" in rendered.extracted_text
    assert cv.layout_metrics["font_size_pt"] >= 9.5
    assert cv.layout_metrics["line_height_ratio"] >= 1.1
    assert cv.layout_metrics["layout_trials"] >= 1
    assert not (tmp_path / ".layout-trials").exists()


def test_renderer_rejects_letter_outside_policy_budget(tmp_path: Path) -> None:
    oversized = _letter().model_copy(update={"paragraphs": ["Evidence " * 450]})

    with pytest.raises(ValueError, match="above_max_characters"):
        DocumentRenderer().render_letter(_snapshot(), oversized, tmp_path)


def test_semantic_fragment_matching_tolerates_pdf_line_break_hyphenation() -> None:
    rendered = _normalize_matching_text(
        "Production: budgets, Procurement et coordination four-\nnisseurs, reporting"
    )

    assert _semantic_fragment_is_present("Procurement et coordination fournisseurs", rendered)
    assert _semantic_fragment_is_present("Procurement", rendered)
    assert not _semantic_fragment_is_present("Procurement et coordination transporteurs", rendered)
