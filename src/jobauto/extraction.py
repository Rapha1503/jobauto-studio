from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import trafilatura

from jobauto.models import ApplicationRow

MIN_DESCRIPTION_LENGTH = 800
MIN_EXCEL_FALLBACK_LENGTH = 350


@dataclass(frozen=True)
class ExtractedOffer:
    text: str
    source: str


class OfferExtractor:
    def __init__(
        self,
        fetch: Callable[[str], str] | None = None,
        browser_fetch: Callable[[str], str] | None = None,
    ) -> None:
        self._fetch = fetch or self._http_fetch
        self._browser_fetch = browser_fetch or self._playwright_fetch

    def extract(self, row: ApplicationRow) -> ExtractedOffer:
        excel_description = strip_offer_boilerplate(row.description if row.description else "")
        if description_looks_complete(excel_description):
            return ExtractedOffer(text=excel_description, source="excel")
        try:
            html = self._fetch(row.url)
            text = self._extract_text(html)
        except httpx.HTTPError:
            text = ""
        source = "http"
        browser_error: Exception | None = None
        if len(text) < MIN_DESCRIPTION_LENGTH:
            try:
                text = self._extract_text(self._browser_fetch(row.url))
            except Exception as exc:
                browser_error = exc
                text = ""
            source = "browser"
        if len(text) < MIN_DESCRIPTION_LENGTH:
            if len(excel_description) >= MIN_EXCEL_FALLBACK_LENGTH:
                return ExtractedOffer(text=excel_description, source="excel_fallback")
            raise RuntimeError(
                "Unable to extract a complete job description; fill Excel column Description"
            ) from browser_error
        return ExtractedOffer(text=text, source=source)

    @staticmethod
    def _extract_text(html: str) -> str:
        text = (
            trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            or ""
        )
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return strip_offer_boilerplate(text)

    @staticmethod
    def _http_fetch(url: str) -> str:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        }
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30)
        response.raise_for_status()
        return response.text

    @staticmethod
    def _playwright_fetch(url: str) -> str:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
                return page.content()
            finally:
                browser.close()


def description_looks_complete(text: str) -> bool:
    if len(text.strip()) < MIN_DESCRIPTION_LENGTH:
        return False
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )
    duty_markers = (
        "descriptif du poste",
        "vos missions",
        "missions",
        "responsibilities",
        "what you will do",
        "the role",
        "about the role",
        "in this role",
        "your mission",
        "job description",
    )
    profile_markers = (
        "profil recherche",
        "votre profil",
        "competences attendues",
        "qualifications",
        "requirements",
        "who you are",
        "about you",
        "what you'll bring",
        "your profile",
    )
    return any(marker in normalized for marker in duty_markers) and any(
        marker in normalized for marker in profile_markers
    )


def strip_offer_boilerplate(text: str) -> str:
    cleaned = text.strip()
    marker = re.search(
        r"(?im)^\s*(?:ces entreprises recrutent aussi|similar jobs|these companies are also hiring|discover similar jobs)\b.*$",
        cleaned,
    )
    if marker is not None:
        cleaned = cleaned[: marker.start()].rstrip()
    return cleaned
