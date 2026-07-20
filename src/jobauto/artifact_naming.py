from __future__ import annotations

import re
import unicodedata


def approved_artifact_stem(
    *,
    kind: str,
    first_name: str,
    last_name: str,
    role: str,
    company: str,
) -> str:
    prefix = "CV" if kind == "cv" else "Lettre"
    segments = [
        prefix,
        _portable_segment(first_name, fallback="Candidat", maximum=40),
        _portable_segment(last_name, fallback="Candidat", maximum=40),
        _portable_segment(role, fallback="Poste", maximum=64),
        _portable_segment(company, fallback="Entreprise", maximum=64),
    ]
    return "_".join(segments)


def _portable_segment(value: str, *, fallback: str, maximum: int) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    compact = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", ascii_value)).strip("_")
    return (compact or fallback)[:maximum].rstrip("_")
