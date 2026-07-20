from __future__ import annotations

import re
from dataclasses import dataclass

from jobauto.adaptation_policy import CvLayoutPolicy

_LAYOUT_MARKER = "% JOBAUTO_LAYOUT"
_PDF_TEXT_MAPPING_MARKER = "% JOBAUTO_PDF_TEXT_MAPPING"
_SECTION_SPACING_MARKER = "% JOBAUTO_SECTION_SPACING"
_LINE_COMMAND_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)(?P<command>\\(?P<name>[A-Za-z@]+)\*?"
    r"(?:\[[^\]]*\])?\{)",
    re.IGNORECASE | re.MULTILINE,
)
_NON_STRUCTURAL_SECTION_COMMANDS = {"sectionmark"}


@dataclass(frozen=True)
class CvLayoutChoice:
    font_size_pt: float
    line_height_ratio: float

    @property
    def baseline_skip_pt(self) -> float:
        return round(self.font_size_pt * self.line_height_ratio, 2)


def cv_layout_choices(policy: CvLayoutPolicy) -> list[CvLayoutChoice]:
    font_sizes: list[float] = []
    current = policy.maximum_font_size_pt
    while current >= policy.minimum_font_size_pt:
        font_sizes.append(round(current, 2))
        current = round(current - 0.5, 2)
    minimum = round(policy.minimum_font_size_pt, 2)
    if minimum not in font_sizes:
        font_sizes.append(minimum)

    ratio_minimum = round(policy.minimum_line_height_ratio, 3)
    ratio_maximum = round(policy.maximum_line_height_ratio, 3)
    ratio_middle = round((ratio_minimum + ratio_maximum) / 2, 3)
    ratio_values = {ratio_minimum, ratio_middle, ratio_maximum}
    current_ratio = round(ratio_maximum - 0.1, 3)
    while current_ratio > ratio_minimum:
        ratio_values.add(current_ratio)
        current_ratio = round(current_ratio - 0.1, 3)
    ratios = sorted(ratio_values, reverse=True)
    choices = [
        CvLayoutChoice(font_size_pt=font_size, line_height_ratio=ratio)
        for font_size in font_sizes
        for ratio in ratios
    ]
    return sorted(
        choices,
        key=lambda choice: _balanced_readability_key(choice, policy),
        reverse=True,
    )


def _balanced_readability_key(
    choice: CvLayoutChoice,
    policy: CvLayoutPolicy,
) -> tuple[float, float, float, float]:
    """Prefer larger type while retaining comfortable leading."""
    font_ratio = choice.font_size_pt / policy.maximum_font_size_pt
    line_ratio = choice.line_height_ratio / policy.maximum_line_height_ratio
    return (
        0.7 * font_ratio + 0.3 * line_ratio,
        min(font_ratio, line_ratio),
        font_ratio + line_ratio,
        choice.line_height_ratio,
    )


def apply_cv_layout(tex: str | bytes, choice: CvLayoutChoice) -> str | bytes:
    is_bytes = isinstance(tex, bytes)
    had_bom = is_bytes and tex.startswith(b"\xef\xbb\xbf")
    text = tex.decode("utf-8-sig") if is_bytes else tex
    if _LAYOUT_MARKER in text:
        raise ValueError("CV source already contains a JobAuto layout override")
    marker = r"\begin{document}"
    if marker not in text:
        raise ValueError("CV source has no \\begin{document} marker")
    newline = "\r\n" if "\r\n" in text else "\n"
    override = newline.join(
        [
            _PDF_TEXT_MAPPING_MARKER,
            r"\ifdefined\pdfgentounicode",
            r"\IfFileExists{glyphtounicode.tex}{\input{glyphtounicode}\pdfgentounicode=1}{}",
            r"\fi",
            _LAYOUT_MARKER,
            rf"\fontsize{{{choice.font_size_pt:g}}}{{{choice.baseline_skip_pt:g}}}\selectfont",
        ]
    )
    rendered = text.replace(marker, marker + newline + override, 1)
    if not is_bytes:
        return rendered
    encoded = rendered.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if had_bom else encoded


def apply_cv_section_spacing(
    tex: str | bytes,
    spacing_pt: float,
    *,
    section_commands: set[str] | frozenset[str] = frozenset(),
) -> str | bytes:
    if spacing_pt <= 0:
        return tex
    is_bytes = isinstance(tex, bytes)
    had_bom = is_bytes and tex.startswith(b"\xef\xbb\xbf")
    text = tex.decode("utf-8-sig") if is_bytes else tex
    if _SECTION_SPACING_MARKER in text:
        raise ValueError("CV source already contains a JobAuto section-spacing override")
    document_marker = r"\begin{document}"
    document_start = text.find(document_marker)
    if document_start < 0:
        raise ValueError("CV source has no \\begin{document} marker")
    body_start = document_start + len(document_marker)
    preamble = text[:body_start]
    body = text[body_start:]
    configured = {name.casefold() for name in section_commands}
    matches = []
    for match in _LINE_COMMAND_PATTERN.finditer(body):
        name = match.group("name").casefold()
        if name in _NON_STRUCTURAL_SECTION_COMMANDS:
            continue
        if name in configured or name.startswith("section") or name.endswith("section"):
            matches.append(match)
    if len(matches) < 2:
        return tex
    newline = "\r\n" if "\r\n" in text else "\n"
    insertion = (
        f"{_SECTION_SPACING_MARKER}{newline}"
        rf"\vspace{{{spacing_pt:g}pt}}"
        f"{newline}"
    )
    positions = {match.start() for match in matches[1:]}
    rendered_body = _LINE_COMMAND_PATTERN.sub(
        lambda match: insertion + match.group(0) if match.start() in positions else match.group(0),
        body,
    )
    rendered = preamble + rendered_body
    if not is_bytes:
        return rendered
    encoded = rendered.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if had_bom else encoded
