from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from jobauto.models import ProjectDraft

PROJECT_HEADER_MAX_CHARS = 110
PROJECT_BULLET_MAX_CHARS = 240
# Below this coverage, a CV that already uses the largest permitted layout needs
# a content review: relevant source sections may have been omitted or shortened.
# This is a review signal, not permission to invent filler.
CV_MIN_VERTICAL_COVERAGE_RATIO = 0.82
CV_MAX_INTERNAL_GAP_RATIO = 0.20


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    message: str
    severity: str = "hard"
    repairable: bool = True


def validate_cv_project_presentation_budget(projects: list[ProjectDraft]) -> CheckResult:
    oversized: list[str] = []
    for project in projects:
        header_length = len(f"{project.title} | {project.stack}")
        bullet_length = len(project.bullet)
        if header_length > PROJECT_HEADER_MAX_CHARS or bullet_length > PROJECT_BULLET_MAX_CHARS:
            oversized.append(
                f"{project.key}(header={header_length}/{PROJECT_HEADER_MAX_CHARS}, "
                f"bullet={bullet_length}/{PROJECT_BULLET_MAX_CHARS})"
            )
    return CheckResult(
        "cv_project_presentation_budget",
        not oversized,
        "oversized=" + (", ".join(oversized) if oversized else "none"),
    )


def extract_pdf_text(path: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        with tempfile.TemporaryDirectory(prefix="jobauto-pdftext-") as temporary:
            short_path = Path(temporary) / "document.pdf"
            shutil.copy2(path, short_path)
            completed = subprocess.run(
                [pdftotext, "-layout", str(short_path), "-"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                return completed.stdout
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def pdf_layout_metrics(path: Path) -> dict[str, int | float | None]:
    reader = PdfReader(str(path))
    positions: list[tuple[float, float]] = []
    page_positions: list[list[float]] = []
    text_fragments = 0
    for page in reader.pages:
        page_height = float(page.mediabox.height)
        current_page_positions: list[float] = []

        def visitor(
            text: str,
            _cm: list[float],
            tm: list[float],
            _font: dict[str, object] | None,
            _font_size: float,
            page_height: float = page_height,
            current_page_positions: list[float] = current_page_positions,
        ) -> None:
            nonlocal text_fragments
            if not text.strip() or len(tm) < 6:
                return
            y = float(tm[5])
            if 0.0 <= y <= page_height:
                positions.append((y, page_height))
                current_page_positions.append(y / page_height)
                text_fragments += 1

        page.extract_text(visitor_text=visitor)
        page_positions.append(current_page_positions)
    if not positions:
        return {
            "pages": len(reader.pages),
            "text_fragments": 0,
            "top_whitespace_ratio": None,
            "bottom_whitespace_ratio": None,
            "vertical_coverage_ratio": None,
            "largest_internal_gap_ratio": None,
        }
    normalized_y = [y / height for y, height in positions if height > 0]
    minimum = min(normalized_y)
    maximum = max(normalized_y)
    largest_internal_gap = max(
        (
            right - left
            for values in page_positions
            for left, right in zip(sorted(set(values)), sorted(set(values))[1:], strict=False)
        ),
        default=0.0,
    )
    return {
        "pages": len(reader.pages),
        "text_fragments": text_fragments,
        "top_whitespace_ratio": round(max(0.0, 1.0 - maximum), 4),
        "bottom_whitespace_ratio": round(max(0.0, minimum), 4),
        "vertical_coverage_ratio": round(maximum - minimum, 4),
        "largest_internal_gap_ratio": round(largest_internal_gap, 4),
    }


def cv_layout_requires_density_review(
    metrics: dict[str, int | float | None],
    *,
    at_layout_ceiling: bool,
) -> bool:
    coverage = metrics.get("vertical_coverage_ratio")
    internal_gap = metrics.get("largest_internal_gap_ratio")
    return bool(
        (isinstance(internal_gap, float) and internal_gap > CV_MAX_INTERNAL_GAP_RATIO)
        or (
            at_layout_ceiling
            and isinstance(coverage, float)
            and coverage < CV_MIN_VERTICAL_COVERAGE_RATIO
        )
    )


def validate_extracted_pdf_text_quality(text: str, prefix: str = "") -> CheckResult:
    glued = _glued_text_suspects(text)
    return CheckResult(
        f"{prefix}extracted_text_quality",
        not glued,
        "glued=" + (", ".join(glued[:6]) if glued else "none"),
    )


def _glued_text_suspects(text: str) -> list[str]:
    suspects: list[str] = []
    month_pattern = (
        r"[A-Za-zÀ-ÿ]{6,}(?:january|february|march|april|may|june|july|august|"
        r"september|october|november|december|janvier|fevrier|février|mars|avril|"
        r"mai|juin|juillet|aout|août|septembre|octobre|novembre|decembre|décembre)\d{4}"
    )
    suspects.extend(re.findall(month_pattern, text, flags=re.IGNORECASE))
    for token in re.findall(r"\b[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9/]{16,}\b", text):
        normalized = unicodedata.normalize("NFKD", token.casefold())
        normalized = "".join(
            character for character in normalized if not unicodedata.combining(character)
        )
        if any(marker in normalized for marker in ("profiledata", "dataengineering")):
            suspects.append(token)
    return sorted(set(suspects))


def pdf_page_count(path: Path) -> int:
    return len(PdfReader(str(path)).pages)
