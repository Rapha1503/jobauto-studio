from __future__ import annotations

import os
import re
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

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
        return _audit_text(target.name, target.read_bytes(), deny_terms=deny_terms)
    leaks: list[ReleaseLeak] = []
    for item in sorted(target.rglob("*")):
        if not item.is_file() or item.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        relative_parts = item.relative_to(target).parts
        if any(part in LOCAL_ARTIFACT_DIRECTORIES for part in relative_parts):
            continue
        leaks.extend(
            _audit_text(
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
            if Path(name).suffix.casefold() not in TEXT_SUFFIXES:
                continue
            leaks.extend(_audit_text(name, archive.read(name), deny_terms=deny_terms))
    return leaks


def _audit_tar(path: Path, *, deny_terms: tuple[str, ...]) -> list[ReleaseLeak]:
    leaks: list[ReleaseLeak] = []
    with tarfile.open(path) as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if not member.isfile() or Path(member.name).suffix.casefold() not in TEXT_SUFFIXES:
                continue
            extracted = archive.extractfile(member)
            if extracted is not None:
                leaks.extend(_audit_text(member.name, extracted.read(), deny_terms=deny_terms))
    return leaks


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
    return leaks


def _looks_like_version_or_date(value: str) -> bool:
    compact = re.sub(r"\s+", "", value)
    digits = re.sub(r"\D", "", compact)
    if "0000" in digits:
        return True
    return bool(
        re.fullmatch(r"(?:19|20)\d{2}[-./](?:0?[1-9]|1[0-2])[-./](?:0?[1-9]|[12]\d|3[01])", compact)
    )
