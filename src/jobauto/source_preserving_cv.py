from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobauto.candidate_snapshot import CandidateSnapshot
from jobauto.cv_source import CvSourceDocument
from jobauto.latex_cv_source import (
    LatexCvMapping,
    TexBlockKind,
    TexCvBlock,
    apply_block_replacements,
)


class LatexBlockReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=2, max_length=80)
    latex: str = Field(min_length=1, max_length=50_000)
    source_ids: list[str] = Field(min_length=1)


class LatexCvPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacements: list[LatexBlockReplacement] = Field(min_length=1)

    @model_validator(mode="after")
    def replacements_are_unique(self) -> LatexCvPatch:
        block_ids = [replacement.block_id for replacement in self.replacements]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("LaTeX CV patch contains duplicate block IDs")
        source_ids = [
            source_id for replacement in self.replacements for source_id in replacement.source_ids
        ]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("LaTeX CV patch contains duplicate source IDs")
        return self


def merge_latex_cv_patch(base: LatexCvPatch, repair: LatexCvPatch) -> LatexCvPatch:
    """Replace only repaired blocks while preserving accepted replacements exactly."""
    base_by_id = {item.block_id: item for item in base.replacements}
    repair_by_id = {item.block_id: item for item in repair.replacements}
    unknown = sorted(set(repair_by_id) - set(base_by_id))
    if unknown:
        raise ValueError(f"technical LaTeX repair targets unknown blocks: {unknown}")
    for block_id, replacement in repair_by_id.items():
        if set(replacement.source_ids) != set(base_by_id[block_id].source_ids):
            raise ValueError(f"technical LaTeX repair changed semantic source IDs: {block_id}")
    return LatexCvPatch(
        replacements=[repair_by_id.get(item.block_id, item) for item in base.replacements]
    )


_COMMAND_PATTERN = re.compile(r"\\([A-Za-z@]+|.)")
_ENVIRONMENT_PATTERN = re.compile(r"\\(begin|end)\s*\{([^{}]+)\}")
_SENSITIVE_COMMANDS = {
    "catcode",
    "csname",
    "def",
    "documentclass",
    "immediate",
    "include",
    "input",
    "lstinputlisting",
    "newcommand",
    "openin",
    "openout",
    "read",
    "renewcommand",
    "special",
    "usepackage",
    "verbatiminput",
    "write",
    "write18",
}
_TEXT_ESCAPE_COMMANDS = {"&", "%", "$", "#", "_", "{", "}"}
_INLINE_GLYPH_COMMANDS = {
    # These commands render a character already represented in the semantic CV.
    # They do not define the template structure and may legitimately be replaced
    # by the equivalent Unicode glyph during an adaptation.
    "euro",
    "texteuro",
}
_INLINE_GLYPH_REPLACEMENTS = {
    "euro": "€",
    "texteuro": "€",
}


def validate_latex_cv_patch(
    snapshot: CandidateSnapshot,
    patch: LatexCvPatch,
    provenance: Mapping[str, tuple[str, ...]],
    semantic_document: CvSourceDocument,
    semantic_source_blocks: Mapping[str, str] | None = None,
) -> None:
    mapping = _required_mapping(snapshot)
    blocks = {block.block_id: block for block in mapping.blocks}
    expected_source_ids = set(provenance)
    actual_source_ids: set[str] = set()

    for replacement in patch.replacements:
        try:
            block = blocks[replacement.block_id]
        except KeyError as exc:
            raise ValueError(f"Unknown LaTeX CV block: {replacement.block_id}") from exc
        headline_only_identity = _is_headline_only_identity_change(block, replacement)
        if not block.policy.capabilities.rephrase and not headline_only_identity:
            raise ValueError(f"LaTeX CV block is locked: {block.block_id}")
        original = snapshot.cv_template_bytes[block.start_byte : block.end_byte].decode("utf-8")
        _require_safe_structure(original, replacement.latex, block)
        if headline_only_identity:
            _require_identity_shell_preserved(
                original,
                replacement.latex,
                snapshot.cv_source.headline,
                semantic_document.headline,
                block.block_id,
            )
        for source_id in replacement.source_ids:
            if source_id not in provenance:
                raise ValueError(f"Unknown semantic CV source ID: {source_id}")
            expected_kind = _kind_for_source_id(source_id, blocks)
            if block.kind is not expected_kind:
                raise ValueError(
                    f"LaTeX block {block.block_id} cannot render {source_id}: "
                    f"expected {expected_kind.value}, got {block.kind.value}"
                )
            if source_id.startswith("source_block.") and source_id != (
                f"source_block.{block.block_id}"
            ):
                raise ValueError(
                    f"LaTeX block {block.block_id} cannot render custom source {source_id}"
                )
            actual_source_ids.add(source_id)
        _require_section_header_preserved(snapshot.cv_template_bytes, block, replacement.latex)
        _require_no_semantic_additions(
            block,
            original,
            replacement.latex,
            replacement.source_ids,
            semantic_document,
            semantic_source_blocks or {},
        )

    missing = sorted(expected_source_ids - actual_source_ids)
    extra = sorted(actual_source_ids - expected_source_ids)
    if missing or extra:
        raise ValueError(
            f"LaTeX CV patch does not match semantic changes: missing={missing}, extra={extra}"
        )


def render_source_preserving_cv(
    snapshot: CandidateSnapshot,
    patch: LatexCvPatch,
    provenance: Mapping[str, tuple[str, ...]],
    semantic_document: CvSourceDocument,
    semantic_source_blocks: Mapping[str, str] | None = None,
) -> bytes:
    validate_latex_cv_patch(
        snapshot,
        patch,
        provenance,
        semantic_document,
        semantic_source_blocks,
    )
    mapping = _required_mapping(snapshot)
    replacements = {
        item.block_id: _normalize_line_endings(item.latex, mapping) for item in patch.replacements
    }
    return apply_block_replacements(snapshot.cv_template_bytes, mapping, replacements)


def latex_cv_prompt_blocks(
    snapshot: CandidateSnapshot,
    required_source_ids: list[str] | tuple[str, ...] = (),
    semantic_source_blocks: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    mapping = _required_mapping(snapshot)
    source = snapshot.cv_template_bytes
    headline_required = "headline.text" in required_source_ids
    return [
        {
            "block_id": block.block_id,
            "kind": block.kind.value,
            "label": block.label,
            "fidelity": block.policy.fidelity.value,
            "target_lines": block.policy.target_lines,
            "latex": source[block.start_byte : block.end_byte].decode("utf-8"),
            "required_layout_commands": _structure_commands(
                source[block.start_byte : block.end_byte].decode("utf-8")
            ),
            "required_environments": _environments(
                source[block.start_byte : block.end_byte].decode("utf-8")
            ),
            "adapted_visible_text": (semantic_source_blocks or {}).get(
                f"source_block.{block.block_id}"
            ),
        }
        for block in mapping.blocks
        if block.policy.capabilities.rephrase
        or (block.kind is TexBlockKind.IDENTITY and headline_required)
    ]


def _is_headline_only_identity_change(block, replacement: LatexBlockReplacement) -> bool:
    return block.kind is TexBlockKind.IDENTITY and replacement.source_ids == ["headline.text"]


def _require_identity_shell_preserved(
    original: str,
    replacement: str,
    original_headline: str,
    adapted_headline: str,
    block_id: str,
) -> None:
    """Allow a role headline change without exposing name or contact details."""
    original_shell = _identity_shell_text(original, original_headline)
    replacement_shell = _identity_shell_text(replacement, adapted_headline)
    if original_shell != replacement_shell:
        raise ValueError(f"LaTeX CV identity details changed outside headline: {block_id}")


def _identity_shell_text(latex: str, headline: str) -> str:
    visible = _normalized_visible_text(latex)
    normalized_headline = _normalized_visible_text(headline)
    if normalized_headline not in visible:
        raise ValueError("LaTeX CV identity block does not contain the expected headline")
    return " ".join(visible.replace(normalized_headline, "", 1).split())


def _normalized_visible_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", _visible_latex_text(value)).casefold()
    return " ".join(normalized.split())


def _required_mapping(snapshot: CandidateSnapshot) -> LatexCvMapping:
    mapping = snapshot.cv_mapping
    if mapping is None:
        raise ValueError("source-preserving CV requires a LaTeX mapping")
    return mapping


def _kind_for_source_id(
    source_id: str,
    blocks: Mapping[str, TexCvBlock],
) -> TexBlockKind:
    prefix = source_id.split(".", maxsplit=1)[0]
    if prefix == "source_block":
        block_id = source_id.removeprefix("source_block.")
        block = blocks.get(block_id)
        if block is None:
            raise ValueError(f"Unknown custom semantic CV source ID: {source_id}")
        return block.kind
    return {
        "headline": TexBlockKind.IDENTITY,
        "summary": TexBlockKind.SUMMARY,
        "experience": TexBlockKind.EXPERIENCE,
        "projects": TexBlockKind.PROJECTS,
        "skills": TexBlockKind.SKILLS,
        "education": TexBlockKind.EDUCATION,
        "languages": TexBlockKind.LANGUAGES,
        "interests": TexBlockKind.INTERESTS,
    }[prefix]


def _require_section_header_preserved(source: bytes, block, replacement: str) -> None:
    if block.kind is TexBlockKind.IDENTITY:
        return
    original = source[block.start_byte : block.end_byte].decode("utf-8")
    original_header = _first_content_line(original)
    replacement_header = _first_content_line(replacement)
    if replacement_header != original_header:
        raise ValueError(f"LaTeX CV section header changed: {block.block_id}")


def _require_safe_structure(original: str, replacement: str, block) -> None:
    if re.search(r"\\(?:textbf|textit|emph)\s*\{\s*\}", replacement):
        raise ValueError(f"LaTeX CV block contains empty visible formatting: {block.block_id}")
    for character in ("&", "#"):
        if _unescaped_count(replacement, character) > _unescaped_count(original, character):
            raise ValueError(
                f"LaTeX CV block contains an unescaped special character {character}: "
                f"{block.block_id}"
            )
    original_commands = _commands(original)
    replacement_commands = _commands(replacement)
    original_sensitive = [
        command for command in original_commands if command in _SENSITIVE_COMMANDS
    ]
    replacement_sensitive = [
        command for command in replacement_commands if command in _SENSITIVE_COMMANDS
    ]
    if replacement_sensitive != original_sensitive:
        raise ValueError(f"LaTeX CV block contains a forbidden command: {block.block_id}")

    original_environments = _environments(original)
    replacement_environments = _environments(replacement)
    if block.policy.capabilities.replace:
        if not set(replacement_environments).issubset(set(original_environments)):
            raise ValueError(f"LaTeX CV block introduces an environment: {block.block_id}")
        new_commands = set(replacement_commands) - set(original_commands)
        if new_commands:
            raise ValueError(
                f"LaTeX CV block introduces commands {sorted(new_commands)}: {block.block_id}"
            )
        return
    for glyph in set(_INLINE_GLYPH_REPLACEMENTS.values()):
        original_count = _inline_glyph_count(original, glyph)
        replacement_count = _inline_glyph_count(replacement, glyph)
        if replacement_count != original_count:
            raise ValueError(
                f"LaTeX CV block changed visible glyph {glyph}: {block.block_id}; "
                f"expected_count={original_count}; actual_count={replacement_count}"
            )
    original_structure = _structure_commands(original)
    replacement_structure = _structure_commands(replacement)
    if replacement_structure != original_structure:
        raise ValueError(
            f"LaTeX CV block command structure changed: {block.block_id}; "
            + _sequence_difference(original_structure, replacement_structure)
        )
    if replacement_environments != original_environments:
        raise ValueError(f"LaTeX CV block environment structure changed: {block.block_id}")


def _require_no_semantic_additions(
    block,
    original: str,
    replacement: str,
    source_ids: list[str],
    document: CvSourceDocument,
    semantic_source_blocks: Mapping[str, str],
) -> None:
    custom_source_id = f"source_block.{block.block_id}"
    custom_expected = semantic_source_blocks.get(custom_source_id)
    if custom_expected is not None:
        actual = _normalized_visible_text(replacement)
        header = _normalized_visible_text(block.label)
        if actual.startswith(header):
            actual = actual[len(header) :].strip()
        expected = _normalized_visible_text(custom_expected)
        if actual != expected:
            raise ValueError(
                f"LaTeX CV custom block does not exactly match the semantic draft: {block.block_id}"
            )
        return
    expected = _semantic_block_text(block.kind, document)
    if block.kind is TexBlockKind.SUMMARY and "summary.text" in source_ids:
        actual = _normalized_visible_text(replacement)
        header = _normalized_visible_text(block.label)
        if actual.startswith(header):
            actual = actual[len(header) :].strip()
        if actual != _normalized_visible_text(expected):
            raise ValueError(
                "LaTeX CV block adds content outside the semantic draft: "
                f"{block.block_id}: summary replacement is not exact"
            )
        return
    allowed_tokens = _claim_tokens(f"{block.label}\n{expected}\n{_visible_latex_text(original)}")
    actual_tokens = _claim_tokens(_visible_latex_text(replacement))
    unexpected = sorted(actual_tokens - allowed_tokens)
    if unexpected:
        raise ValueError(
            f"LaTeX CV block adds content outside the semantic draft: "
            f"{block.block_id}: {unexpected}"
        )


def _semantic_block_text(kind: TexBlockKind, document: CvSourceDocument) -> str:
    if kind is TexBlockKind.IDENTITY:
        return "\n".join((document.name, document.headline, document.contact_line))
    if kind is TexBlockKind.SUMMARY:
        return document.summary
    if kind is TexBlockKind.SKILLS:
        return "\n".join(f"{label}: {', '.join(items)}" for label, items in document.skills.items())
    if kind is TexBlockKind.LANGUAGES:
        return document.languages
    if kind is TexBlockKind.INTERESTS:
        return document.interests
    if kind in {TexBlockKind.EXPERIENCE, TexBlockKind.PROJECTS, TexBlockKind.EDUCATION}:
        entries = getattr(document, kind.value)
        return "\n".join(
            value
            for entry in entries
            for value in (entry.title, entry.dates, entry.stack, *entry.bullets)
            if value
        )
    return ""


def _visible_latex_text(value: str) -> str:
    text = re.sub(r"(?<!\\)%.*", " ", value)
    for command, glyph in _INLINE_GLYPH_REPLACEMENTS.items():
        text = re.sub(rf"\\{command}\s*\{{\s*\}}", glyph, text)
    text = re.sub(
        r"\\(?:vspace|hspace|rule|fontsize)\*?\s*\{[^{}]*\}",
        " ",
        text,
    )
    text = _ENVIRONMENT_PATTERN.sub(" ", text)
    text = re.sub(r"\[[^\[\]]*\]", " ", text)
    for escaped in _TEXT_ESCAPE_COMMANDS:
        text = text.replace(f"\\{escaped}", escaped)
    text = re.sub(r"\\[A-Za-z@]+\*?", " ", text)
    text = re.sub(r"\\.", " ", text)
    return text.translate(str.maketrans({"{": " ", "}": " ", "$": " "}))


def _claim_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_like = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return {
        token
        for token in re.findall(r"[a-z0-9]+", ascii_like)
        if len(token) >= 4 or (len(token) >= 2 and any(character.isdigit() for character in token))
        if not re.fullmatch(r"\d+(?:pt|cm|mm|em|ex|in)", token)
    }


def _commands(value: str) -> list[str]:
    return [
        command.casefold()
        for command in _COMMAND_PATTERN.findall(value)
        if command not in _TEXT_ESCAPE_COMMANDS
    ]


def _structure_commands(value: str) -> list[str]:
    """Return commands that define layout rather than a visible text glyph."""
    return [
        command
        for command in _commands(value)
        if command not in _INLINE_GLYPH_COMMANDS and (command == "\\" or command[:1].isalpha())
    ]


def _sequence_difference(expected: list[str], actual: list[str]) -> str:
    mismatch = next(
        (
            index
            for index, (expected_item, actual_item) in enumerate(
                zip(expected, actual, strict=False)
            )
            if expected_item != actual_item
        ),
        min(len(expected), len(actual)),
    )
    return f"first_difference={mismatch}; expected_commands={expected}; actual_commands={actual}"


def _inline_glyph_count(value: str, glyph: str) -> int:
    count = value.count(glyph)
    for command, replacement in _INLINE_GLYPH_REPLACEMENTS.items():
        if replacement == glyph:
            count += len(re.findall(rf"\\{command}\s*\{{\s*\}}", value))
    return count


def _environments(value: str) -> list[str]:
    return [
        f"{boundary.casefold()}:{name.strip().casefold()}"
        for boundary, name in _ENVIRONMENT_PATTERN.findall(value)
    ]


def _unescaped_count(value: str, character: str) -> int:
    return len(re.findall(rf"(?<!\\){re.escape(character)}", value))


def _first_content_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "")


def _normalize_line_endings(value: str, mapping: LatexCvMapping) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized.replace("\n", "\r\n") if mapping.line_ending == "crlf" else normalized
