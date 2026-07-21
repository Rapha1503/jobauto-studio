from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlsplit
from uuid import uuid4

import httpx
import trafilatura
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from jobauto.candidate_context import CandidateContext
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.codex_client import GenerationPhase
from jobauto.run_store import utc_now
from jobauto.studio_campaign import StudioCampaignRecord, StudioCampaignService

DiscoveryStatus = Literal[
    "prepared",
    "ready_for_codex",
    "running",
    "cancelling",
    "cancelled",
    "campaign_created",
    "blocked",
]
AvailabilityStatus = Literal["available", "unavailable", "unknown"]
ContentVerificationStatus = Literal["verified", "unverified"]
SOURCE_CLAIMED_PRIMARY = "codex_web_claimed_primary"
SOURCE_HTTP_AVAILABLE = "codex_web_claimed_primary_http_available"
SOURCE_HTTP_UNVERIFIED = "codex_web_claimed_primary_http_unverified"
SOURCE_HTTP_CONTENT_VERIFIED = "codex_web_http_content_verified"
_MIN_EXTRACTED_OFFER_CHARACTERS = 200
_CLOSED_OFFER_MARKERS = (
    "cette offre n est plus disponible",
    "cette annonce n est plus disponible",
    "ce poste n est plus disponible",
    "offre expiree",
    "annonce expiree",
    "poste pourvu",
    "this job is no longer available",
    "this position is no longer available",
    "this job has expired",
    "this position has been filled",
    "job posting has expired",
    "job not found",
    "position not found",
    "this job no longer exists",
    "this position no longer exists",
    "esta oferta ya no esta disponible",
    "la oferta ha caducado",
)


class DiscoveryOffer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=240)
    url: HttpUrl
    posted_at: str | None = None
    posted_text: str | None = None
    location: str | None = None
    language: str | None = None
    description: str = Field(min_length=80)
    source: str = SOURCE_CLAIMED_PRIMARY
    experience_required: str | None = None
    contract_type: str | None = None
    salary_estimate: str | None = None
    semantic_fit_score: int = Field(ge=0, le=100)
    semantic_fit_rationale: str = Field(min_length=20, max_length=500)


class RejectedDiscoveryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = "Unknown"
    role: str = "Unknown"
    url: str | None = None
    reason: str


class DiscoveryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    offers: list[DiscoveryOffer]
    rejected_candidates: list[RejectedDiscoveryCandidate] = Field(default_factory=list)
    notes: str = ""
    end_of_response: bool = Field(alias="END_OF_RESPONSE")


class DiscoveryAgent(Protocol):
    def complete_json(
        self,
        prompt: str,
        response_model: type[DiscoveryBatch],
        phase: GenerationPhase,
    ) -> DiscoveryBatch: ...


class OfferAvailabilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    status: AvailabilityStatus
    status_code: int | None = None
    final_url: str | None = None
    reason: str
    content_status: ContentVerificationStatus = "unverified"
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content_characters: int = Field(default=0, ge=0)
    extracted_description: str | None = Field(default=None, exclude=True)


class OfferAvailabilityVerifier(Protocol):
    def verify(self, url: str) -> OfferAvailabilityResult: ...


class HttpOfferAvailabilityVerifier:
    """Reject terminal HTTP removals without mistaking anti-bot responses for closure."""

    def __init__(self, fetch: Callable[[str], httpx.Response] | None = None) -> None:
        self._fetch = fetch or self._http_get

    def verify(self, url: str) -> OfferAvailabilityResult:
        ashby_reference = _ashby_job_board_reference(url)
        if ashby_reference is not None:
            ashby_result = self._verify_ashby_posting(url, *ashby_reference)
            if ashby_result is not None:
                return ashby_result
        try:
            response = self._fetch(url)
        except httpx.HTTPError as exc:
            return OfferAvailabilityResult(
                url=url,
                status="unknown",
                reason=f"HTTP verification could not complete: {type(exc).__name__}",
            )
        status_code = response.status_code
        final_url = str(response.url)
        if status_code in {404, 410}:
            return OfferAvailabilityResult(
                url=url,
                status="unavailable",
                status_code=status_code,
                final_url=final_url,
                reason=f"Offer URL returned terminal HTTP {status_code}.",
            )
        if 200 <= status_code < 400 and _redirect_lost_offer_path(url, final_url):
            return OfferAvailabilityResult(
                url=url,
                status="unavailable",
                status_code=status_code,
                final_url=final_url,
                reason="Offer URL redirected from a job path to the site homepage.",
            )
        if 200 <= status_code < 400:
            closed_marker = _closed_offer_marker(response.text)
            if closed_marker is not None:
                return OfferAvailabilityResult(
                    url=url,
                    status="unavailable",
                    status_code=status_code,
                    final_url=final_url,
                    reason=f"Offer page states that the position is closed: {closed_marker!r}.",
                )
            extracted_description = _extract_offer_description(response.text)
            if extracted_description is not None:
                return OfferAvailabilityResult(
                    url=url,
                    status="available",
                    status_code=status_code,
                    final_url=final_url,
                    reason=f"Offer URL returned HTTP {status_code} with extractable content.",
                    content_status="verified",
                    content_sha256=hashlib.sha256(
                        extracted_description.encode("utf-8")
                    ).hexdigest(),
                    content_characters=len(extracted_description),
                    extracted_description=extracted_description,
                )
            return OfferAvailabilityResult(
                url=url,
                status="available",
                status_code=status_code,
                final_url=final_url,
                reason=f"Offer URL returned HTTP {status_code}.",
            )
        return OfferAvailabilityResult(
            url=url,
            status="unknown",
            status_code=status_code,
            final_url=final_url,
            reason=f"HTTP {status_code} is not reliable proof that the offer is closed.",
        )

    def _verify_ashby_posting(
        self,
        url: str,
        job_board_name: str,
        posting_id: str,
    ) -> OfferAvailabilityResult | None:
        endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{quote(job_board_name, safe='')}"
        try:
            response = self._fetch(endpoint)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs, list):
            return None
        for job in jobs:
            if not isinstance(job, dict) or not _ashby_job_matches(job, posting_id):
                continue
            description = _validated_extracted_offer_text(job.get("descriptionPlain"))
            final_url = str(job.get("jobUrl") or url)
            return OfferAvailabilityResult(
                url=url,
                status="available",
                status_code=response.status_code,
                final_url=final_url,
                reason="Ashby public job board API confirms that the offer is published.",
                content_status="verified" if description is not None else "unverified",
                content_sha256=(
                    hashlib.sha256(description.encode("utf-8")).hexdigest()
                    if description is not None
                    else None
                ),
                content_characters=len(description or ""),
                extracted_description=description,
            )
        return OfferAvailabilityResult(
            url=url,
            status="unavailable",
            status_code=response.status_code,
            final_url=url,
            reason="Offer is absent from the currently published Ashby job board.",
        )

    @staticmethod
    def _http_get(url: str) -> httpx.Response:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        }
        with httpx.Client(headers=headers, follow_redirects=True, timeout=15) as client:
            return client.get(url)


def _extract_offer_description(html: str) -> str | None:
    json_ld_description = _extract_jobposting_json_ld(html)
    if json_ld_description is not None:
        return json_ld_description
    extracted = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    return _validated_extracted_offer_text(extracted)


def _extract_jobposting_json_ld(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for node in _walk_json_ld(payload):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if not any(str(value).casefold() == "jobposting" for value in types):
                continue
            parts = [
                node.get("description"),
                node.get("responsibilities"),
                node.get("qualifications"),
                node.get("skills"),
            ]
            text = "\n".join(
                BeautifulSoup(str(value), "html.parser").get_text("\n")
                for value in parts
                if isinstance(value, str) and value.strip()
            )
            validated = _validated_extracted_offer_text(text)
            if validated is not None:
                return validated
    return None


def _walk_json_ld(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_ld(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_ld(child)


def _validated_extracted_offer_text(text: str | None) -> str | None:
    if text is None:
        return None
    lines = [" ".join(line.split()) for line in text.splitlines()]
    normalized = "\n".join(line for line in lines if line).strip()
    if len(normalized) < _MIN_EXTRACTED_OFFER_CHARACTERS:
        return None
    return normalized


def _closed_offer_marker(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    visible_start = " ".join(soup.get_text(" ", strip=True).split())[:4_000]
    decomposed = unicodedata.normalize("NFKD", visible_start)
    normalized = "".join(char for char in decomposed if not unicodedata.combining(char))
    normalized = normalized.casefold().replace("’", " ").replace("'", " ")
    normalized = " ".join(normalized.split())
    return next((marker for marker in _CLOSED_OFFER_MARKERS if marker in normalized), None)


def _redirect_lost_offer_path(original_url: str, final_url: str) -> bool:
    original = urlsplit(original_url)
    final = urlsplit(final_url)
    original_path = original.path.rstrip("/")
    final_path = final.path.rstrip("/")
    return bool(
        original.hostname
        and original.hostname.casefold() == (final.hostname or "").casefold()
        and original_path
        and not final_path
        and not final.query
    )


def _ashby_job_board_reference(url: str) -> tuple[str, str] | None:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != "jobs.ashbyhq.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _ashby_job_matches(job: dict[str, object], posting_id: str) -> bool:
    for field in ("jobUrl", "applyUrl"):
        candidate = job.get(field)
        if not isinstance(candidate, str):
            continue
        reference = _ashby_job_board_reference(candidate)
        if reference is not None and reference[1].casefold() == posting_id.casefold():
            return True
    return False


class CampaignService(Protocol):
    def create(
        self,
        *,
        profile_path: Path,
        tracker_path: Path,
        candidates: list[dict[str, object]],
        limit: int,
    ) -> StudioCampaignRecord: ...


class DiscoveryHandoffRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discovery_id: str = Field(min_length=8, max_length=160)
    candidate_id: str = Field(min_length=2, max_length=80)
    profile_path: Path
    tracker_path: Path
    status: DiscoveryStatus
    phase: str = "prepared"
    requested_count: int = Field(ge=1, le=100)
    conversation_url: HttpUrl | None = None
    created_at: str
    updated_at: str
    discovery_dir: Path
    prompt_path: Path
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    campaign_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    candidate_count: int = Field(default=0, ge=0)
    cancel_requested_at: str | None = None
    cancelled_at: str | None = None


class DiscoveryCancelled(RuntimeError):
    pass


class DiscoveryHandoffStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, record: DiscoveryHandoffRecord) -> DiscoveryHandoffRecord:
        record.discovery_dir.mkdir(parents=True, exist_ok=False)
        return self.save(record)

    def save(self, record: DiscoveryHandoffRecord) -> DiscoveryHandoffRecord:
        if not record.discovery_dir.is_dir():
            raise FileNotFoundError(f"discovery directory does not exist: {record.discovery_dir}")
        _atomic_write_json(
            record.discovery_dir / "discovery.json",
            record.model_dump(mode="json"),
        )
        return record

    def get(self, discovery_id: str) -> DiscoveryHandoffRecord:
        path = self.root / discovery_id / "discovery.json"
        if not path.is_file():
            raise FileNotFoundError(f"Studio discovery not found: {discovery_id}")
        return DiscoveryHandoffRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_for_candidate(
        self,
        candidate_id: str,
        *,
        limit: int = 10,
    ) -> list[DiscoveryHandoffRecord]:
        records: list[DiscoveryHandoffRecord] = []
        for path in self.root.glob("*/discovery.json"):
            try:
                record = DiscoveryHandoffRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            if record.candidate_id == candidate_id:
                records.append(record)
        return sorted(records, key=lambda record: record.updated_at, reverse=True)[:limit]


class DiscoveryHandoffService:
    def __init__(
        self,
        *,
        repository: CandidateProfileRepository,
        campaign_service: StudioCampaignService | CampaignService,
        store: DiscoveryHandoffStore,
        agent_factory: Callable[[Callable[[dict[str, Any]], None]], DiscoveryAgent] | None = None,
        availability_verifier: OfferAvailabilityVerifier | None = None,
    ) -> None:
        self.repository = repository
        self.campaign_service = campaign_service
        self.store = store
        self.agent_factory = agent_factory
        self.availability_verifier = availability_verifier

    def prepare(
        self,
        *,
        profile_path: Path,
        tracker_path: Path,
        requested_count: int,
        conversation_url: str | None = None,
    ) -> DiscoveryHandoffRecord:
        tracker = tracker_path.expanduser().resolve()
        if tracker.suffix.casefold() != ".xlsx" or not tracker.is_file():
            raise ValueError("tracker must be an existing .xlsx workbook")
        snapshot = self.repository.load_snapshot(profile_path)
        discovery_id = f"{snapshot.profile.candidate_id}-{uuid4().hex[:12]}"
        discovery_dir = self.store.root / discovery_id
        prompt = build_discovery_prompt(
            CandidateContext.from_snapshot(snapshot),
            tracker_path=tracker,
            requested_count=requested_count,
        )
        prompt_path = discovery_dir / "prompt.txt"
        now = utc_now()
        record = DiscoveryHandoffRecord(
            discovery_id=discovery_id,
            candidate_id=snapshot.profile.candidate_id,
            profile_path=profile_path.expanduser().resolve(),
            tracker_path=tracker,
            status="prepared",
            phase="prepared",
            requested_count=requested_count,
            conversation_url=conversation_url,
            created_at=now,
            updated_at=now,
            discovery_dir=discovery_dir,
            prompt_path=prompt_path,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            snapshot_hash=snapshot.snapshot_hash,
        )
        self.store.create(record)
        prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
        return record

    def execute(
        self,
        discovery_id: str,
    ) -> tuple[DiscoveryHandoffRecord, StudioCampaignRecord]:
        record = self.store.get(discovery_id)
        if record.status not in {"prepared", "ready_for_codex"}:
            raise ValueError(f"discovery cannot start from status: {record.status}")
        if self.agent_factory is None:
            return self._block(record, "Codex web discovery is not configured")
        record = self._transition(record, status="running", phase="searching_web")

        def record_event(event: dict[str, Any]) -> None:
            current = self.store.get(discovery_id)
            events = [*current.events, {"at": utc_now(), **event}]
            self.store.save(current.model_copy(update={"events": events, "updated_at": utc_now()}))

        try:
            batch = self.agent_factory(record_event).complete_json(
                record.prompt_path.read_text(encoding="utf-8"),
                DiscoveryBatch,
                GenerationPhase.DISCOVERY,
            )
            self._ensure_not_cancelled(discovery_id)
            if not batch.end_of_response:
                raise ValueError("Codex discovery response is incomplete")
            _atomic_write_json(
                record.discovery_dir / "agent-result.json",
                batch.model_dump(mode="json", by_alias=True),
            )
            record = self._transition(
                self.store.get(discovery_id),
                status="running",
                phase="verifying_offer_urls",
                candidate_count=len(batch.offers),
            )
            candidates, checks = self._verify_offer_urls(batch.offers, record_event)
            self._ensure_not_cancelled(discovery_id)
            if checks:
                _atomic_write_json(
                    record.discovery_dir / "availability.json",
                    {"checks": [check.model_dump(mode="json") for check in checks]},
                )
            record = self._transition(
                self.store.get(discovery_id),
                status="running",
                phase="deduplicating_and_scoring",
                candidate_count=len(candidates),
            )
            return self.import_candidates(
                discovery_id,
                candidates=[offer.model_dump(mode="json") for offer in candidates],
            )
        except DiscoveryCancelled:
            self._mark_cancelled(discovery_id)
            raise
        except Exception as exc:
            self._block(self.store.get(discovery_id), f"{type(exc).__name__}: {exc}")
            raise

    def import_candidates(
        self,
        discovery_id: str,
        *,
        candidates: list[dict[str, object]],
    ) -> tuple[DiscoveryHandoffRecord, StudioCampaignRecord]:
        record = self.store.get(discovery_id)
        self._ensure_not_cancelled(discovery_id)
        if record.status not in {"prepared", "ready_for_codex", "running"}:
            raise ValueError(f"discovery is not ready for import: {record.status}")
        if not candidates:
            raise ValueError("discovery result contains no offers")
        snapshot = self.repository.load_snapshot(record.profile_path)
        if snapshot.snapshot_hash != record.snapshot_hash:
            raise ValueError("candidate profile changed after discovery was prepared")
        campaign = self.campaign_service.create(
            profile_path=record.profile_path,
            tracker_path=record.tracker_path,
            candidates=candidates,
            limit=record.requested_count,
        )
        imported_path = record.discovery_dir / "candidates.json"
        _atomic_write_json(imported_path, {"offers": candidates})
        updated = record.model_copy(
            update={
                "status": "campaign_created",
                "phase": "documents_running",
                "campaign_id": campaign.campaign_id,
                "updated_at": utc_now(),
            }
        )
        return self.store.save(updated), campaign

    def get(self, discovery_id: str) -> DiscoveryHandoffRecord:
        return self.store.get(discovery_id)

    def retry_interrupted(self, discovery_id: str) -> DiscoveryHandoffRecord:
        record = self.store.get(discovery_id)
        if (
            record.status not in {"prepared", "ready_for_codex", "running", "blocked"}
            or record.campaign_id is not None
        ):
            raise ValueError(f"discovery cannot be resumed from status: {record.status}")
        events = [
            *record.events,
            {
                "at": utc_now(),
                "model": "jobauto",
                "phase": "discovery",
                "status": "resuming_after_interruption",
            },
        ]
        return self.store.save(
            record.model_copy(
                update={
                    "status": "ready_for_codex",
                    "phase": "prepared",
                    "blockers": [],
                    "events": events,
                    "cancel_requested_at": None,
                    "cancelled_at": None,
                    "updated_at": utc_now(),
                }
            )
        )

    def request_cancel(self, discovery_id: str) -> DiscoveryHandoffRecord:
        record = self.store.get(discovery_id)
        if record.status in {"campaign_created", "cancelled"}:
            raise ValueError(f"discovery cannot be cancelled from status: {record.status}")
        now = utc_now()
        if record.status in {"prepared", "ready_for_codex", "blocked"}:
            status: DiscoveryStatus = "cancelled"
            phase = "cancelled"
            cancelled_at = now
        else:
            status = "cancelling"
            phase = record.phase
            cancelled_at = None
        events = [
            *record.events,
            {
                "at": now,
                "model": "jobauto",
                "phase": "discovery",
                "status": "cancel_requested",
            },
        ]
        return self.store.save(
            record.model_copy(
                update={
                    "status": status,
                    "phase": phase,
                    "cancel_requested_at": now,
                    "cancelled_at": cancelled_at,
                    "events": events,
                    "updated_at": now,
                }
            )
        )

    def _ensure_not_cancelled(self, discovery_id: str) -> None:
        if self.store.get(discovery_id).cancel_requested_at is not None:
            raise DiscoveryCancelled("discovery cancelled by the user")

    def _mark_cancelled(self, discovery_id: str) -> DiscoveryHandoffRecord:
        record = self.store.get(discovery_id)
        now = utc_now()
        return self.store.save(
            record.model_copy(
                update={
                    "status": "cancelled",
                    "phase": "cancelled",
                    "cancelled_at": now,
                    "updated_at": now,
                }
            )
        )

    def _verify_offer_urls(
        self,
        offers: list[DiscoveryOffer],
        record_event: Callable[[dict[str, Any]], None],
    ) -> tuple[list[DiscoveryOffer], list[OfferAvailabilityResult]]:
        if self.availability_verifier is None:
            return offers, []
        accepted: list[DiscoveryOffer] = []
        checks: list[OfferAvailabilityResult] = []
        for offer in offers:
            check = self.availability_verifier.verify(str(offer.url))
            checks.append(check)
            record_event(
                {
                    "phase": "offer_availability",
                    "company": offer.company,
                    "role": offer.role,
                    **check.model_dump(mode="json"),
                }
            )
            if check.status == "available":
                if check.extracted_description is not None:
                    accepted.append(
                        offer.model_copy(
                            update={
                                "description": check.extracted_description,
                                "source": SOURCE_HTTP_CONTENT_VERIFIED,
                            }
                        )
                    )
                else:
                    accepted.append(offer.model_copy(update={"source": SOURCE_HTTP_AVAILABLE}))
            elif check.status == "unknown":
                accepted.append(offer.model_copy(update={"source": SOURCE_HTTP_UNVERIFIED}))
        if not accepted:
            raise ValueError("discovery verification rejected every offer as unavailable")
        return accepted, checks

    def _transition(
        self,
        record: DiscoveryHandoffRecord,
        *,
        status: DiscoveryStatus,
        phase: str,
        candidate_count: int | None = None,
    ) -> DiscoveryHandoffRecord:
        update: dict[str, object] = {
            "status": status,
            "phase": phase,
            "updated_at": utc_now(),
        }
        if candidate_count is not None:
            update["candidate_count"] = candidate_count
        return self.store.save(record.model_copy(update=update))

    def _block(
        self,
        record: DiscoveryHandoffRecord,
        message: str,
    ):
        blocked = record.model_copy(
            update={
                "status": "blocked",
                "phase": "blocked",
                "blockers": [message],
                "updated_at": utc_now(),
            }
        )
        self.store.save(blocked)
        if self.agent_factory is None:
            raise RuntimeError(message)
        return blocked


def build_discovery_prompt(
    context: CandidateContext,
    *,
    tracker_path: Path,
    requested_count: int,
) -> str:
    payload = context.payload
    preferences = payload["search_preferences"]
    skills = payload["skill_policy"]
    profile_summary = {
        "availability": payload.get("availability"),
        "locale": payload.get("locale"),
        "experience_history": [
            {
                "title": item.get("title"),
                "dates": item.get("dates"),
                "bullets": item.get("bullets", []),
            }
            for item in payload.get("baseline_cv", {}).get("experience", [])
        ],
        "education": [
            {"title": item.get("title"), "dates": item.get("dates")}
            for item in payload.get("baseline_cv", {}).get("education", [])
        ],
        "verified_skills": skills.get("verified", {}),
        "transferable_skills": skills.get("transferable", {}),
        "project_titles": [project["title"] for project in payload.get("projects", [])],
        "verified_evidence": [
            {
                "claim": fact.get("claim"),
                "keywords": fact.get("keywords", []),
                "role_tags": fact.get("role_tags", []),
            }
            for fact in payload.get("facts", [])
        ],
        "additional_cv_sections": [
            {
                "label": block.get("label"),
                "content": block.get("latex"),
            }
            for block in payload.get("additional_evidence_blocks", [])
        ],
        "search_preferences": preferences,
        "work_authorization": payload.get("work_authorization"),
    }
    output_contract = {
        "offers": [
            {
                "company": "...",
                "role": "...",
                "url": "https://official-career-or-ats.example/job/123",
                "posted_at": "YYYY-MM-DD or null",
                "posted_text": "source text or explicit uncertainty",
                "location": "...",
                "language": "fr or en",
                "description": "full offer text needed for ATS and document adaptation",
                "source": SOURCE_CLAIMED_PRIMARY,
                "experience_required": "...",
                "contract_type": "...",
                "salary_estimate": "estimate or null",
                "semantic_fit_score": 0,
                "semantic_fit_rationale": "concise role, sector and evidence-based fit judgment",
            }
        ],
        "rejected_candidates": [{"company": "...", "role": "...", "url": "...", "reason": "..."}],
        "notes": "short uncertainties only",
        "END_OF_RESPONSE": True,
    }
    return (
        "You are the job-discovery agent for JobAuto Studio. Complete the search before "
        "answering; do not ask for confirmation and do not return partial progress.\n\n"
        f"TARGET: return up to {max(requested_count * 3, requested_count + 5)} currently "
        f"actionable candidates so Studio can retain {requested_count} new offers after local "
        "deduplication and scoring. Search wider than the target and verify every result.\n\n"
        "DEDUPLICATION: avoid duplicate URLs inside this result. Studio owns the existing-offer "
        "tracker and performs the authoritative cross-campaign deduplication locally.\n\n"
        "VERIFICATION: open the detailed official career or official ATS page. Keep an offer "
        "only when the role, employer, full description and application path are visible and "
        "the page does not say closed, expired, unavailable or not found. Unknown metadata must "
        "stay null or be described as uncertain; never invent it.\n\n"
        "MATCHING: interpret required, preferred and avoided criteria semantically. Hard-reject "
        "only explicit contradictions. Missing metadata is uncertainty, not automatic rejection. "
        "A mandatory credential absent from the supplied candidate evidence is an explicit contradiction; "
        "this includes required degrees, licences, work authorization and minimum experience in "
        "the named specialty. Do not infer an absent credential from adjacent skills. "
        "Use verified and transferable skills to judge plausible fit, without requiring every "
        "listed technology.\n\n"
        "ROLE FIT AND ORDERING: compare the central day-to-day function, expected outputs and "
        "candidate evidence, not isolated shared words. Reject a role whose core function is "
        "outside the configured target families even when its sector, location or generic terms "
        "overlap. Give every retained offer a semantic_fit_score and a short evidence-based "
        "semantic_fit_rationale. Order retained offers from strongest to weakest semantic fit.\n\n"
        f"CANDIDATE CONFIGURATION:\n{json.dumps(profile_summary, ensure_ascii=False, indent=2)}\n\n"
        f"LOCAL TRACKER (handled by Studio, do not open it):\n{tracker_path}\n\n"
        "OUTPUT: strict JSON only, no Markdown, no preamble, no progress messages. Preserve the "
        "complete offer description because downstream ATS analysis reads that text directly.\n"
        f"{json.dumps(output_contract, ensure_ascii=False, indent=2)}\n"
    )


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
