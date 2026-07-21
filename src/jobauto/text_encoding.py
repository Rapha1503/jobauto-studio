from __future__ import annotations

import re

_MOJIBAKE_MARKERS = ("\u00c3", "\u00c2", "\u00e2", "\u00f0", "\u0178", "\u00ef\u00b8")


def repair_utf8_mojibake(value: str) -> str:
    """Repair UTF-8 decoded as Windows-1252 without touching valid Unicode."""

    def marker_score(text: str) -> int:
        return sum(text.count(marker) for marker in _MOJIBAKE_MARKERS)

    def repair_token(token: str) -> str:
        if not any(marker in token for marker in _MOJIBAKE_MARKERS):
            return token
        current = token
        for _attempt in range(2):
            try:
                candidate = current.encode("cp1252").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            if marker_score(candidate) >= marker_score(current):
                break
            current = candidate
        return current

    return "".join(repair_token(token) for token in re.split(r"([ \t\r\n]+)", value))


def repair_mojibake_data(value):
    if isinstance(value, str):
        return repair_utf8_mojibake(value)
    if isinstance(value, list):
        return [repair_mojibake_data(item) for item in value]
    if isinstance(value, dict):
        return {
            repair_utf8_mojibake(key) if isinstance(key, str) else key: repair_mojibake_data(item)
            for key, item in value.items()
        }
    return value
