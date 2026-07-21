from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jobauto.adaptation_policy import (
    STUDIO_ADAPTATION_PRESETS,
    FidelityLevel,
    SectionPolicy,
)


class TexBlockKind(StrEnum):
    IDENTITY = "identity"
    SUMMARY = "summary"
    EXPERIENCE = "experience"
    PROJECTS = "projects"
    SKILLS = "skills"
    EDUCATION = "education"
    LANGUAGES = "languages"
    INTERESTS = "interests"
    OTHER = "other"


class TexCvBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    label: str = Field(min_length=1, max_length=120)
    kind: TexBlockKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    detector: str = Field(min_length=1, max_length=80)
    policy: SectionPolicy

    @model_validator(mode="after")
    def range_is_valid(self) -> TexCvBlock:
        if self.end_line < self.start_line:
            raise ValueError("end_line cannot precede start_line")
        if self.end_byte < self.start_byte:
            raise ValueError("end_byte cannot precede start_byte")
        return self


class LatexCvMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    filename: str = Field(min_length=1, max_length=240)
    encoding: str = "utf-8"
    line_ending: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preamble_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    document_start_line: int = Field(ge=1)
    document_end_line: int = Field(ge=1)
    preamble_end_byte: int = Field(ge=0)
    blocks: list[TexCvBlock]

    @property
    def mapping_hash(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @model_validator(mode="after")
    def blocks_do_not_overlap(self) -> LatexCvMapping:
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("CV block IDs must be unique")
        ordered = sorted(self.blocks, key=lambda block: (block.start_byte, block.end_byte))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if current.start_byte < previous.end_byte:
                raise ValueError(f"CV blocks overlap: {previous.block_id} and {current.block_id}")
        return self

    def write(self, path: Path) -> None:
        path.write_text(
            json.dumps(self.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> LatexCvMapping:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for block in payload.get("blocks", []):
            if "policy" not in block:
                block["policy"] = _default_policy(TexBlockKind(block["kind"])).model_dump(
                    mode="json"
                )
        return cls.model_validate(payload)


class TexBlockCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    label: str = Field(min_length=1, max_length=120)
    kind: TexBlockKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    fidelity: FidelityLevel
    required: bool = True
    target_lines: int | None = Field(default=None, ge=1, le=200)


_SECTION_ALIASES: dict[str, TexBlockKind] = {
    "contact": TexBlockKind.IDENTITY,
    "contact details": TexBlockKind.IDENTITY,
    "coordonnees": TexBlockKind.IDENTITY,
    "personal details": TexBlockKind.IDENTITY,
    "personal information": TexBlockKind.IDENTITY,
    "resume": TexBlockKind.SUMMARY,
    "summary": TexBlockKind.SUMMARY,
    "profile": TexBlockKind.SUMMARY,
    "profil": TexBlockKind.SUMMARY,
    "experience": TexBlockKind.EXPERIENCE,
    "experiences": TexBlockKind.EXPERIENCE,
    "professional experience": TexBlockKind.EXPERIENCE,
    "experience professionnelle": TexBlockKind.EXPERIENCE,
    "projects": TexBlockKind.PROJECTS,
    "projets": TexBlockKind.PROJECTS,
    "skills": TexBlockKind.SKILLS,
    "competences": TexBlockKind.SKILLS,
    "technical skills": TexBlockKind.SKILLS,
    "education": TexBlockKind.EDUCATION,
    "formation": TexBlockKind.EDUCATION,
    "languages": TexBlockKind.LANGUAGES,
    "langues": TexBlockKind.LANGUAGES,
    "interests": TexBlockKind.INTERESTS,
    "centres d interet": TexBlockKind.INTERESTS,
}

_COMMAND_WITH_LABEL = re.compile(
    r"\\(?P<command>[A-Za-z@]+)\*?(?:\[[^\]]*\])?\{(?P<label>[^{}]{1,120})\}"
)
_SECTION_COMMANDS = {"cvsection", "section", "subsection"}
MAX_TEX_SOURCE_BYTES = 2_000_000


def _is_section_command(command: str) -> bool:
    return (
        command in _SECTION_COMMANDS or command.startswith("section") or command.endswith("section")
    )


_MACRO_DEFINITION = re.compile(
    r"\\(?:re)?newcommand\*?\s*\{?\\(?P<name>[A-Za-z@]+)\}?\s*"
    r"(?:\[(?P<arity>\d+)\])?",
    re.IGNORECASE,
)
_SECTION_STYLE_TOKENS = re.compile(
    r"\\(?:section|subsection|rule|hrule|large|Large|LARGE|bfseries|MakeUppercase)\b",
)


def _declared_section_commands(preamble: str) -> set[str]:
    """Find one-argument display macros that behave like section headings."""
    matches = list(_MACRO_DEFINITION.finditer(preamble))
    commands: set[str] = set()
    for index, match in enumerate(matches):
        if match.group("arity") != "1":
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(preamble)
        definition = preamble[match.end() : min(end, match.end() + 800)]
        if _SECTION_STYLE_TOKENS.search(definition):
            commands.add(match.group("name").casefold())
    return commands


def analyze_latex_cv(source: bytes, *, filename: str) -> LatexCvMapping:
    if not filename.casefold().endswith(".tex"):
        raise ValueError("CV source filename must end with .tex")
    if not source:
        raise ValueError("CV source is empty")
    if len(source) > MAX_TEX_SOURCE_BYTES:
        raise ValueError(f"CV source exceeds {MAX_TEX_SOURCE_BYTES} bytes")
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CV source must use UTF-8 encoding") from exc
    raw_without_bom = text.encode("utf-8")
    bom_size = len(source) - len(raw_without_bom)
    if bom_size not in {0, 3}:
        raise ValueError("Unsupported CV source encoding")

    lines = source.splitlines(keepends=True)
    decoded_lines = [_decode_line(line, index + 1) for index, line in enumerate(lines)]
    line_offsets = _line_offsets(lines)
    begin_index = _find_document_boundary(decoded_lines, r"\begin{document}")
    end_index = _find_document_boundary(decoded_lines, r"\end{document}")
    if end_index <= begin_index:
        raise ValueError("LaTeX document boundaries are invalid")

    declared_section_commands = _declared_section_commands(
        "".join(decoded_lines[: begin_index + 1])
    )
    section_headers: list[tuple[int, str, TexBlockKind, str]] = []
    for index in range(begin_index + 1, end_index):
        visible = _strip_latex_comment(decoded_lines[index])
        for match in _COMMAND_WITH_LABEL.finditer(visible):
            label = _plain_label(match.group("label"))
            kind = _SECTION_ALIASES.get(label)
            command = match.group("command").casefold()
            if (
                kind is not None
                or _is_section_command(command)
                or command in declared_section_commands
            ):
                section_headers.append(
                    (
                        index,
                        match.group("label").strip(),
                        kind or TexBlockKind.OTHER,
                        match.group("command"),
                    )
                )
                break

    blocks: list[TexCvBlock] = []
    first_section_index = section_headers[0][0] if section_headers else end_index
    identity_start = begin_index + 1
    if first_section_index > identity_start and any(
        _strip_latex_comment(line).strip()
        for line in decoded_lines[identity_start:first_section_index]
    ):
        blocks.append(
            _block_from_lines(
                block_id="identity",
                label="Identity",
                kind=TexBlockKind.IDENTITY,
                start_index=identity_start,
                end_index=first_section_index,
                line_offsets=line_offsets,
                detector="document-prefix",
                confidence=0.75,
                policy=_default_policy(TexBlockKind.IDENTITY),
            )
        )

    counters: dict[TexBlockKind, int] = {}
    for position, (header_index, label, kind, command) in enumerate(section_headers):
        next_header = (
            section_headers[position + 1][0] if position + 1 < len(section_headers) else end_index
        )
        counters[kind] = counters.get(kind, 0) + 1
        suffix = "" if counters[kind] == 1 else f"-{counters[kind]}"
        blocks.append(
            _block_from_lines(
                block_id=f"{kind.value}{suffix}",
                label=label,
                kind=kind,
                start_index=header_index,
                end_index=next_header,
                line_offsets=line_offsets,
                detector=f"semantic-command:{command}",
                confidence=0.9,
                policy=_default_policy(kind),
            )
        )

    preamble_end_byte = line_offsets[begin_index]
    return LatexCvMapping(
        filename=Path(filename).name,
        line_ending=_line_ending(source),
        source_sha256=_sha256(source),
        preamble_sha256=_sha256(source[:preamble_end_byte]),
        document_start_line=begin_index + 1,
        document_end_line=end_index + 1,
        preamble_end_byte=preamble_end_byte,
        blocks=blocks,
    )


def corrected_mapping(
    source: bytes,
    mapping: LatexCvMapping,
    corrections: list[TexBlockCorrection],
) -> LatexCvMapping:
    _require_source_hash(source, mapping)
    lines = source.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    blocks = [
        _block_from_lines(
            block_id=item.block_id,
            label=item.label,
            kind=item.kind,
            start_index=item.start_line - 1,
            end_index=item.end_line,
            line_offsets=offsets,
            detector="user-confirmed",
            confidence=1.0,
            policy=SectionPolicy(
                fidelity=item.fidelity,
                required=item.required,
                target_lines=item.target_lines,
            ),
        )
        for item in corrections
    ]
    for block in blocks:
        if block.start_line <= mapping.document_start_line:
            raise ValueError("CV blocks must start after \\begin{document}")
        if block.end_line >= mapping.document_end_line:
            raise ValueError("CV blocks must end before \\end{document}")
    payload = mapping.model_dump(mode="python")
    payload["blocks"] = blocks
    return LatexCvMapping.model_validate(payload)


def apply_block_replacements(
    source: bytes,
    mapping: LatexCvMapping,
    replacements: dict[str, str],
) -> bytes:
    validate_mapping_source(source, mapping)
    by_id = {block.block_id: block for block in mapping.blocks}
    unknown = sorted(set(replacements) - set(by_id))
    if unknown:
        raise KeyError(f"Unknown CV block ids: {unknown}")
    result = source
    selected = [(by_id[block_id], value) for block_id, value in replacements.items()]
    for block, replacement in sorted(selected, key=lambda item: item[0].start_byte, reverse=True):
        encoded = replacement.encode("utf-8")
        result = result[: block.start_byte] + encoded + result[block.end_byte :]
    if _sha256(result[: mapping.preamble_end_byte]) != mapping.preamble_sha256:
        raise ValueError("CV preamble changed during source-preserving patch")
    return result


def validate_mapping_source(source: bytes, mapping: LatexCvMapping) -> None:
    """Validate a sidecar against the exact source before it can authorize edits."""
    _require_source_hash(source, mapping)
    lines = source.splitlines(keepends=True)
    decoded_lines = [_decode_line(line, index + 1) for index, line in enumerate(lines)]
    offsets = _line_offsets(lines)
    begin_index = _find_document_boundary(decoded_lines, r"\begin{document}")
    end_index = _find_document_boundary(decoded_lines, r"\end{document}")
    if mapping.document_start_line != begin_index + 1:
        raise ValueError("CV mapping has an invalid document start")
    if mapping.document_end_line != end_index + 1:
        raise ValueError("CV mapping has an invalid document end")
    if mapping.preamble_end_byte != offsets[begin_index]:
        raise ValueError("CV mapping has an invalid preamble boundary")
    if _sha256(source[: mapping.preamble_end_byte]) != mapping.preamble_sha256:
        raise ValueError("CV mapping preamble hash does not match source")
    for block in mapping.blocks:
        if block.start_line <= mapping.document_start_line:
            raise ValueError(f"CV block starts outside document body: {block.block_id}")
        if block.end_line >= mapping.document_end_line:
            raise ValueError(f"CV block ends outside document body: {block.block_id}")
        if block.end_line >= len(offsets):
            raise ValueError(f"CV block line range exceeds source: {block.block_id}")
        if block.start_byte != offsets[block.start_line - 1]:
            raise ValueError(f"CV block start byte does not match line range: {block.block_id}")
        if block.end_byte != offsets[block.end_line]:
            raise ValueError(f"CV block end byte does not match line range: {block.block_id}")


def _block_from_lines(
    *,
    block_id: str,
    label: str,
    kind: TexBlockKind,
    start_index: int,
    end_index: int,
    line_offsets: list[int],
    detector: str,
    confidence: float,
    policy: SectionPolicy,
) -> TexCvBlock:
    if start_index < 0 or end_index <= start_index or end_index >= len(line_offsets):
        raise ValueError(f"Invalid CV block line range: {start_index + 1}-{end_index}")
    return TexCvBlock(
        block_id=block_id,
        label=label,
        kind=kind,
        start_line=start_index + 1,
        end_line=end_index,
        start_byte=line_offsets[start_index],
        end_byte=line_offsets[end_index],
        confidence=confidence,
        detector=detector,
        policy=policy,
    )


def _line_offsets(lines: list[bytes]) -> list[int]:
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _decode_line(line: bytes, line_number: int) -> str:
    try:
        return line.decode("utf-8-sig" if line_number == 1 else "utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"CV source is not valid UTF-8 at line {line_number}") from exc


def _find_document_boundary(lines: list[str], marker: str) -> int:
    for index, line in enumerate(lines):
        if marker in _strip_latex_comment(line):
            return index
    raise ValueError(f"CV source is missing {marker}")


def _strip_latex_comment(line: str) -> str:
    for index, character in enumerate(line):
        if character == "%" and (index == 0 or line[index - 1] != "\\"):
            return line[:index]
    return line


def _plain_label(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value.casefold())
    ascii_value = "".join(char for char in normalized if not unicodedata.combining(char))
    ascii_value = re.sub(r"\\[A-Za-z@]+", " ", ascii_value)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_value).split())


def _line_ending(source: bytes) -> str:
    if b"\r\n" in source:
        return "crlf"
    if b"\n" in source:
        return "lf"
    return "none"


def _require_source_hash(source: bytes, mapping: LatexCvMapping) -> None:
    if _sha256(source) != mapping.source_sha256:
        raise ValueError("CV source changed after block detection")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _default_policy(kind: TexBlockKind) -> SectionPolicy:
    fidelity = STUDIO_ADAPTATION_PRESETS["balanced"][kind.value]
    return SectionPolicy(fidelity=fidelity, required=kind is not TexBlockKind.INTERESTS)
