from __future__ import annotations

import hashlib
import re
import shutil
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from jobauto.adaptation_policy import validate_section_change
from jobauto.build import compile_latex
from jobauto.candidate_profile import CvBackend
from jobauto.candidate_snapshot import CandidateSnapshot
from jobauto.cv_layout import (
    CvLayoutChoice,
    apply_cv_layout,
    apply_cv_section_spacing,
    cv_layout_choices,
)
from jobauto.document_patch import (
    CvDocumentDraft,
    changed_cv_fragments,
    validate_cv_document,
)
from jobauto.generic_cv_renderer import render_profile_cv_tex_source
from jobauto.latex_utils import latex_escape
from jobauto.models import CandidateLetterDraft
from jobauto.public_validation import (
    CV_MAX_INTERNAL_GAP_RATIO,
    CV_MIN_VERTICAL_COVERAGE_RATIO,
    cv_layout_requires_density_review,
    extract_pdf_text,
    pdf_layout_metrics,
    pdf_page_count,
    validate_extracted_pdf_text_quality,
)
from jobauto.source_preserving_cv import render_source_preserving_cv

_EMAIL_PATTERN = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"\+?\d(?:[\s().-]*\d){8,}")


@dataclass(frozen=True)
class RenderedDocument:
    source_path: Path
    pdf_path: Path
    page_count: int
    extracted_text: str
    extracted_text_sha256: str
    pdf_sha256: str
    layout_metrics: dict[str, int | float | None]


class DocumentRenderer:
    def render_cv(
        self,
        snapshot: CandidateSnapshot,
        draft: CvDocumentDraft,
        output_dir: Path,
    ) -> RenderedDocument:
        validate_cv_document(snapshot, draft)
        if snapshot.profile.cv_backend is CvBackend.SOURCE_PRESERVING:
            if draft.latex_patch is None and draft.provenance:
                raise ValueError("source-preserving CV draft requires a LaTeX patch")
            tex = (
                snapshot.cv_template_bytes
                if draft.latex_patch is None
                else render_source_preserving_cv(
                    snapshot,
                    draft.latex_patch,
                    draft.provenance,
                    draft.document,
                    draft.source_blocks,
                )
            )
        else:
            tex = render_profile_cv_tex_source(
                snapshot.cv_template,
                draft.document,
                snapshot.adaptation_policy,
                locale=snapshot.profile.locale,
            )
        rendered = self._compile_cv_with_readable_layout(snapshot, tex, output_dir)
        _validate_rendered_cv_fragments(snapshot, draft, rendered.extracted_text)
        return rendered

    def _compile_cv_with_readable_layout(
        self,
        snapshot: CandidateSnapshot,
        tex: str | bytes,
        output_dir: Path,
    ) -> RenderedDocument:
        layout = snapshot.adaptation_policy.documents["cv"].layout
        if layout is None:
            return self._compile_and_inspect(snapshot, tex, output_dir, stem="cv", maximum_pages=1)

        trial_root = output_dir / ".layout-trials"
        shutil.rmtree(trial_root, ignore_errors=True)
        layout_choices = cv_layout_choices(layout)
        selected: tuple[str | bytes, CvLayoutChoice, int] | None = None
        last_overflow: ValueError | None = None
        try:
            for index, choice in enumerate(layout_choices, start=1):
                candidate = apply_cv_layout(tex, choice)
                try:
                    self._compile_and_inspect(
                        snapshot,
                        candidate,
                        trial_root / f"trial-{index}",
                        stem="cv",
                        maximum_pages=1,
                    )
                except ValueError as exc:
                    if "page count exceeds policy" not in str(exc):
                        raise
                    last_overflow = exc
                    continue
                selected = (candidate, choice, index)
                break
        finally:
            shutil.rmtree(trial_root, ignore_errors=True)

        if selected is None:
            raise ValueError(
                "cv cannot fit on one page within the configured readability bounds"
            ) from last_overflow
        candidate, choice, trial_count = selected
        rendered = self._compile_and_inspect(
            snapshot,
            candidate,
            output_dir,
            stem="cv",
            maximum_pages=1,
        )
        at_layout_ceiling = choice == layout_choices[0]
        section_spacing_pt = 0.0
        section_spacing_trials = 0
        coverage = rendered.layout_metrics.get("vertical_coverage_ratio")
        if isinstance(coverage, float):
            layout_candidate = candidate
            section_commands = {
                block.detector.split(":", 1)[1].split("+", 1)[0]
                for block in (snapshot.cv_mapping.blocks if snapshot.cv_mapping else [])
                if block.detector.startswith("semantic-command:")
            }
            spacing_root = output_dir / ".section-spacing-trials"
            shutil.rmtree(spacing_root, ignore_errors=True)
            try:
                for section_spacing_trials, spacing_pt in enumerate((12.0, 8.0, 4.0, 2.0), start=1):
                    spaced_candidate = apply_cv_section_spacing(
                        layout_candidate,
                        spacing_pt,
                        section_commands=section_commands,
                    )
                    if spaced_candidate == layout_candidate:
                        break
                    try:
                        spaced_rendered = self._compile_and_inspect(
                            snapshot,
                            spaced_candidate,
                            spacing_root / f"trial-{section_spacing_trials}",
                            stem="cv",
                            maximum_pages=1,
                        )
                    except ValueError as exc:
                        if "page count exceeds policy" not in str(exc):
                            raise
                        continue
                    spaced_coverage = spaced_rendered.layout_metrics.get("vertical_coverage_ratio")
                    if isinstance(spaced_coverage, float) and spaced_coverage > coverage:
                        candidate = spaced_candidate
                        coverage = spaced_coverage
                        section_spacing_pt = spacing_pt
                        break
                    if isinstance(spaced_coverage, float) and spaced_coverage <= coverage:
                        break
            finally:
                shutil.rmtree(spacing_root, ignore_errors=True)
            if section_spacing_pt:
                rendered = self._compile_and_inspect(
                    snapshot,
                    candidate,
                    output_dir,
                    stem="cv",
                    maximum_pages=1,
                )
        metrics = dict(rendered.layout_metrics)
        coverage = metrics.get("vertical_coverage_ratio")
        internal_gap = metrics.get("largest_internal_gap_ratio")
        requires_density_review = cv_layout_requires_density_review(
            metrics,
            at_layout_ceiling=at_layout_ceiling,
        )
        metrics.update(
            {
                "font_size_pt": choice.font_size_pt,
                "line_height_ratio": choice.line_height_ratio,
                "baseline_skip_pt": choice.baseline_skip_pt,
                "layout_trials": trial_count,
                "section_spacing_pt": section_spacing_pt,
                "section_spacing_trials": section_spacing_trials,
                "at_layout_ceiling": at_layout_ceiling,
                "unused_vertical_ratio": (
                    round(1.0 - coverage, 4) if isinstance(coverage, float) else None
                ),
                "has_large_internal_gap": bool(
                    isinstance(internal_gap, float) and internal_gap > CV_MAX_INTERNAL_GAP_RATIO
                ),
                "requires_density_review": requires_density_review,
                "underfilled_at_layout_ceiling": bool(
                    at_layout_ceiling
                    and isinstance(coverage, float)
                    and coverage < CV_MIN_VERTICAL_COVERAGE_RATIO
                ),
            }
        )
        return replace(rendered, layout_metrics=metrics)

    def render_letter(
        self,
        snapshot: CandidateSnapshot,
        draft: CandidateLetterDraft,
        output_dir: Path,
    ) -> RenderedDocument:
        draft.validate_for_snapshot(snapshot)
        body = "\n\n".join([draft.greeting, *draft.paragraphs, draft.closing])
        try:
            policy = snapshot.adaptation_policy.documents["letter"].sections["body"]
        except KeyError as exc:
            raise ValueError("adaptation policy has no letter body") from exc
        violations = validate_section_change(
            policy,
            snapshot.letter_reference,
            body,
            used_fact_ids=draft.used_fact_ids,
        )
        if violations:
            details = "; ".join(f"{item.code}: {item.message}" for item in violations)
            raise ValueError(f"Letter violates adaptation policy: {details}")
        return self._compile_and_inspect(
            snapshot,
            _render_letter_tex(draft),
            output_dir,
            stem="letter",
            maximum_pages=1,
        )

    @staticmethod
    def _compile_and_inspect(
        snapshot: CandidateSnapshot,
        tex: str | bytes,
        output_dir: Path,
        *,
        stem: str,
        maximum_pages: int,
    ) -> RenderedDocument:
        target = output_dir.expanduser().resolve()
        target.mkdir(parents=True, exist_ok=True)
        source_path = target / f"{stem}.tex"
        if isinstance(tex, bytes):
            source_path.write_bytes(tex)
        else:
            source_path.write_text(tex, encoding="utf-8", newline="\n")
        pdf_path, _log_path = compile_latex(source_path, target / "build")
        pages = pdf_page_count(pdf_path)
        if pages < 1 or pages > maximum_pages:
            raise ValueError(f"{stem} page count exceeds policy: pages={pages}")
        extracted_text = extract_pdf_text(pdf_path).strip()
        if not extracted_text:
            raise ValueError(f"{stem} PDF has no extractable text")
        identity = snapshot.profile.identity
        expected_name = _normalize_text(f"{identity.first_name} {identity.last_name}")
        if expected_name not in _normalize_text(extracted_text):
            raise ValueError(f"{stem} PDF does not contain candidate identity")
        if stem == "cv":
            if identity.email.casefold() not in extracted_text.casefold():
                raise ValueError("cv PDF does not contain candidate email")
            foreign_emails = sorted(
                {
                    match.casefold()
                    for match in _EMAIL_PATTERN.findall(extracted_text)
                    if match.casefold() != identity.email.casefold()
                }
            )
            if foreign_emails:
                raise ValueError(f"cv PDF contains foreign email addresses: {foreign_emails}")
            if identity.phone is not None and _digits(identity.phone) not in _digits(
                extracted_text
            ):
                raise ValueError("cv PDF does not contain candidate phone")
            if identity.phone is not None:
                expected_phone = _digits(identity.phone)
                foreign_phones = sorted(
                    {
                        digits
                        for match in _PHONE_PATTERN.findall(extracted_text)
                        if len(digits := _digits(match)) >= 9 and digits != expected_phone
                    }
                )
                if foreign_phones:
                    raise ValueError(f"cv PDF contains foreign phone numbers: {foreign_phones}")
        text_quality = validate_extracted_pdf_text_quality(extracted_text, prefix=f"{stem}_")
        if not text_quality.passed:
            raise ValueError(f"{stem} PDF text quality failed: {text_quality.message}")
        return RenderedDocument(
            source_path=source_path,
            pdf_path=pdf_path,
            page_count=pages,
            extracted_text=extracted_text,
            extracted_text_sha256=hashlib.sha256(extracted_text.encode("utf-8")).hexdigest(),
            pdf_sha256=_file_sha256(pdf_path),
            layout_metrics=pdf_layout_metrics(pdf_path),
        )


def _render_letter_tex(draft: CandidateLetterDraft) -> str:
    paragraphs = "\n\n".join(
        rf"\noindent {latex_escape(paragraph)}\par" for paragraph in draft.paragraphs
    )
    closing = r"\\".join(
        latex_escape(line.strip()) for line in draft.closing.splitlines() if line.strip()
    )
    return (
        "\\documentclass[11pt,a4paper]{article}\n"
        "\\usepackage[margin=2.2cm]{geometry}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\ifdefined\\pdfgentounicode\n"
        "\\IfFileExists{glyphtounicode.tex}{\\input{glyphtounicode}\\pdfgentounicode=1}{}\n"
        "\\fi\n"
        "\\setlength{\\parindent}{0pt}\n"
        "\\setlength{\\parskip}{8pt}\n"
        "\\pagestyle{empty}\n"
        "\\begin{document}\n"
        f"{latex_escape(draft.greeting)}\\par\n\n"
        f"{paragraphs}\n\n"
        f"\\vspace{{8pt}}\n{closing}\n"
        "\\end{document}\n"
    )


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(
            character for character in normalized if not unicodedata.combining(character)
        ).split()
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _validate_rendered_cv_fragments(
    snapshot: CandidateSnapshot,
    draft: CvDocumentDraft,
    extracted_text: str,
) -> None:
    rendered = _normalize_matching_text(extracted_text)
    missing = [
        f"{source_id}: {fragment}"
        for source_id, fragments in changed_cv_fragments(snapshot, draft).items()
        for fragment in fragments
        if len(_normalize_matching_text(fragment)) >= 3
        and not _semantic_fragment_is_present(fragment, rendered)
    ]
    if missing:
        raise ValueError(f"cv PDF is missing adapted semantic content: {missing}")


def _normalize_matching_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in normalized
            if not unicodedata.combining(character)
        ).split()
    )


def _semantic_fragment_is_present(fragment: str, normalized_rendered: str) -> bool:
    expected = _normalize_matching_text(fragment)
    if expected in normalized_rendered:
        return True
    compact_expected = expected.replace(" ", "")
    compact_rendered = normalized_rendered.replace(" ", "")
    if len(compact_expected) >= 8 and compact_expected in compact_rendered:
        return True
    tokens = list(dict.fromkeys(token for token in expected.split() if len(token) >= 4))
    if len(tokens) < 6:
        return False
    rendered_tokens = set(normalized_rendered.split())
    covered = sum(token in rendered_tokens for token in tokens)
    return covered / len(tokens) >= 0.9
