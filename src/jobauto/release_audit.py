from __future__ import annotations

import io
import os
import re
import struct
import tarfile
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader

TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".rst",
    ".tex",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
BINARY_AUDIT_SUFFIXES = {".jpeg", ".jpg", ".pdf", ".png"}
AUDIT_SUFFIXES = TEXT_SUFFIXES | BINARY_AUDIT_SUFFIXES

SENSITIVE_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "api_secret": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "windows_user_path": re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+"),
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "phone": re.compile(
        r"(?<![A-Za-z0-9_-])(?:\+\d{1,3}[\s.-]?)?(?:\d[\s().-]?){9,14}(?![A-Za-z0-9_-])"
    ),
}

ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.test"}
LOCAL_ARTIFACT_DIRECTORIES = {
    ".codex_work",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".superpowers",
    ".venv",
    "build",
    "dist",
    "generated",
    "logs",
    "outputs",
    "runs",
    "tmp",
}


@dataclass(frozen=True)
class ReleaseLeak:
    source: str
    kind: str
    excerpt: str


def audit_release_path(path: Path, *, deny_terms: tuple[str, ...] = ()) -> list[ReleaseLeak]:
    target = path.expanduser().resolve()
    if target.suffix == ".whl" or zipfile.is_zipfile(target):
        return _audit_zip(target, deny_terms=deny_terms)
    if target.is_file() and tarfile.is_tarfile(target):
        return _audit_tar(target, deny_terms=deny_terms)
    if target.is_file():
        return _audit_payload(target.name, target.read_bytes(), deny_terms=deny_terms)
    leaks: list[ReleaseLeak] = []
    for item in sorted(target.rglob("*")):
        if not item.is_file() or item.suffix.casefold() not in AUDIT_SUFFIXES:
            continue
        relative_parts = item.relative_to(target).parts
        if any(part in LOCAL_ARTIFACT_DIRECTORIES for part in relative_parts):
            continue
        leaks.extend(
            _audit_payload(
                item.relative_to(target).as_posix(), item.read_bytes(), deny_terms=deny_terms
            )
        )
    return leaks


def configured_deny_terms(extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    configured = tuple(
        item.strip()
        for item in os.getenv("JOBAUTO_RELEASE_DENY_TERMS", "").split("|")
        if item.strip()
    )
    return tuple(dict.fromkeys((*configured, *extra)))


def _audit_zip(path: Path, *, deny_terms: tuple[str, ...]) -> list[ReleaseLeak]:
    leaks: list[ReleaseLeak] = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if Path(name).suffix.casefold() not in AUDIT_SUFFIXES:
                continue
            leaks.extend(_audit_payload(name, archive.read(name), deny_terms=deny_terms))
    return leaks


def _audit_tar(path: Path, *, deny_terms: tuple[str, ...]) -> list[ReleaseLeak]:
    leaks: list[ReleaseLeak] = []
    with tarfile.open(path) as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if not member.isfile() or Path(member.name).suffix.casefold() not in AUDIT_SUFFIXES:
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                leaks.extend(_audit_payload(member.name, extracted.read(), deny_terms=deny_terms))
    return leaks


def _audit_payload(
    source: str, payload: bytes, *, deny_terms: tuple[str, ...]
) -> list[ReleaseLeak]:
    suffix = Path(source).suffix.casefold()
    if suffix == ".pdf":
        return _audit_pdf(source, payload, deny_terms=deny_terms)
    if suffix == ".png":
        return _audit_png_metadata(source, payload, deny_terms=deny_terms)
    if suffix in {".jpeg", ".jpg"}:
        return _audit_jpeg_metadata(source, payload, deny_terms=deny_terms)
    return _audit_text(source, payload, deny_terms=deny_terms)


def _audit_pdf(source: str, payload: bytes, *, deny_terms: tuple[str, ...]) -> list[ReleaseLeak]:
    try:
        reader = PdfReader(io.BytesIO(payload), strict=False)
        parts = [page.extract_text() or "" for page in reader.pages]
        parts.extend(str(value) for value in (reader.metadata or {}).values() if value is not None)
    except Exception as exc:
        return [ReleaseLeak(source, "unreadable_pdf", type(exc).__name__)]
    return _audit_text(source, "\n".join(parts).encode("utf-8"), deny_terms=deny_terms)


def _audit_png_metadata(
    source: str, payload: bytes, *, deny_terms: tuple[str, ...]
) -> list[ReleaseLeak]:
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return [ReleaseLeak(source, "unreadable_png", "invalid signature")]
    cursor = 8
    text_parts: list[str] = []
    saw_header = False
    saw_end = False
    try:
        while cursor + 12 <= len(payload):
            length = struct.unpack(">I", payload[cursor : cursor + 4])[0]
            kind = payload[cursor + 4 : cursor + 8]
            chunk_end = cursor + 12 + length
            if chunk_end > len(payload):
                raise ValueError("truncated chunk")
            data = payload[cursor + 8 : cursor + 8 + length]
            expected_crc = struct.unpack(">I", payload[cursor + 8 + length : chunk_end])[0]
            if zlib.crc32(kind + data) & 0xFFFFFFFF != expected_crc:
                raise ValueError("invalid chunk CRC")
            cursor = chunk_end
            if not saw_header:
                if kind != b"IHDR":
                    raise ValueError("missing IHDR")
                saw_header = True
            if kind == b"tEXt":
                text_parts.append(data.decode("latin-1", errors="replace"))
            elif kind == b"zTXt":
                keyword, compressed = data.split(b"\x00", 1)
                if not compressed or compressed[0] != 0:
                    raise ValueError("invalid zTXt compression method")
                text_parts.append(keyword.decode("latin-1", errors="replace"))
                text_parts.append(
                    zlib.decompress(compressed[1:]).decode("latin-1", errors="replace")
                )
            elif kind == b"iTXt":
                fields = data.split(b"\x00", 5)
                if len(fields) == 6:
                    keyword, compressed_flag, _method, language, translated, content = fields
                    if compressed_flag == b"\x01":
                        content = zlib.decompress(content)
                    elif compressed_flag != b"\x00":
                        raise ValueError("invalid iTXt compression flag")
                    text_parts.extend(
                        item.decode("utf-8", errors="replace")
                        for item in (keyword, language, translated, content)
                    )
            elif kind == b"eXIf":
                text_parts.extend(_metadata_text_variants(data))
            if kind == b"IEND":
                if length != 0 or cursor != len(payload):
                    raise ValueError("invalid IEND")
                saw_end = True
                break
    except (ValueError, struct.error, zlib.error) as exc:
        return [ReleaseLeak(source, "unreadable_png", type(exc).__name__)]
    if not saw_header or not saw_end:
        return [ReleaseLeak(source, "unreadable_png", "incomplete structure")]
    return _audit_text(source, "\n".join(text_parts).encode("utf-8"), deny_terms=deny_terms)


def _audit_jpeg_metadata(
    source: str, payload: bytes, *, deny_terms: tuple[str, ...]
) -> list[ReleaseLeak]:
    if not payload.startswith(b"\xff\xd8"):
        return [ReleaseLeak(source, "unreadable_jpeg", "invalid signature")]
    cursor = 2
    text_parts: list[str] = []
    saw_end = False
    try:
        while cursor < len(payload):
            if payload[cursor] != 0xFF:
                raise ValueError("invalid marker")
            while cursor < len(payload) and payload[cursor] == 0xFF:
                cursor += 1
            if cursor >= len(payload):
                raise ValueError("truncated marker")
            marker = payload[cursor]
            cursor += 1
            if marker == 0xD9:
                saw_end = True
                break
            if marker == 0xDA:
                saw_end = payload.rfind(b"\xff\xd9", cursor) >= cursor
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD8:
                continue
            if cursor + 2 > len(payload):
                raise ValueError("truncated segment")
            length = struct.unpack(">H", payload[cursor : cursor + 2])[0]
            if length < 2 or cursor + length > len(payload):
                raise ValueError("invalid segment length")
            data = payload[cursor + 2 : cursor + length]
            cursor += length
            if marker in {0xE1, 0xED, 0xFE}:
                text_parts.extend(_metadata_text_variants(data))
    except (ValueError, struct.error) as exc:
        return [ReleaseLeak(source, "unreadable_jpeg", type(exc).__name__)]
    if not saw_end:
        return [ReleaseLeak(source, "unreadable_jpeg", "missing EOI")]
    return _audit_text(source, "\n".join(text_parts).encode("utf-8"), deny_terms=deny_terms)


def _metadata_text_variants(payload: bytes) -> list[str]:
    variants = [
        payload.decode("utf-8", errors="ignore"),
        payload.decode("latin-1", errors="ignore"),
    ]
    for offset in (0, 1):
        even_payload = payload[offset : len(payload) - ((len(payload) - offset) % 2)]
        if even_payload:
            variants.extend(
                even_payload.decode(encoding, errors="ignore")
                for encoding in ("utf-16-le", "utf-16-be")
            )
    return variants


def _audit_text(source: str, payload: bytes, *, deny_terms: tuple[str, ...]) -> list[ReleaseLeak]:
    text = payload.decode("utf-8", errors="replace")
    leaks: list[ReleaseLeak] = []
    for term in deny_terms:
        if term.casefold() in text.casefold():
            leaks.append(ReleaseLeak(source, "deny_term", term))
    for kind, pattern in SENSITIVE_PATTERNS.items():
        for match in pattern.finditer(text):
            excerpt = " ".join(match.group(0).split())
            if kind == "email" and excerpt.rsplit("@", 1)[-1].casefold() in ALLOWED_EMAIL_DOMAINS:
                continue
            if kind == "phone" and _looks_like_version_or_date(excerpt):
                continue
            leaks.append(ReleaseLeak(source, kind, excerpt[:120]))
    return list(dict.fromkeys(leaks))


def _looks_like_version_or_date(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    digits = re.sub(r"\D", "", compact)
    if "0000" in digits:
        return True
    if re.fullmatch(r"(?:19|20)\d{12}", digits):
        try:
            datetime.strptime(digits, "%Y%m%d%H%M%S")
        except ValueError:
            return False
        return True
    return bool(
        re.fullmatch(r"(?:19|20)\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])", compact)
    )
