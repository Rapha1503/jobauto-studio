from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openpyxl import Workbook

from jobauto.candidate_context import CandidateContext
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.discovery_handoff import (
    SOURCE_HTTP_CONTENT_VERIFIED,
    DiscoveryBatch,
    DiscoveryCancelled,
    DiscoveryHandoffService,
    DiscoveryHandoffStore,
    DiscoveryOffer,
    HttpOfferAvailabilityVerifier,
    OfferAvailabilityResult,
    build_discovery_prompt,
)


class FakeCampaignService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(campaign_id="campaign-12345678")


class FakeDiscoveryAgent:
    def __init__(self, batch: DiscoveryBatch) -> None:
        self.batch = batch

    def complete_json(self, _prompt, _response_model, _phase):
        return self.batch


class FakeAvailabilityVerifier:
    def __init__(
        self,
        statuses: dict[str, str],
        extracted_descriptions: dict[str, str] | None = None,
    ) -> None:
        self.statuses = statuses
        self.extracted_descriptions = extracted_descriptions or {}

    def verify(self, url: str) -> OfferAvailabilityResult:
        status = self.statuses[url]
        description = self.extracted_descriptions.get(url)
        return OfferAvailabilityResult(
            url=url,
            status=status,
            status_code={"available": 200, "unavailable": 410}.get(status, 403),
            final_url=url,
            reason=f"test {status}",
            content_status="verified" if description else "unverified",
            content_sha256=(
                hashlib.sha256(description.encode("utf-8")).hexdigest() if description else None
            ),
            content_characters=len(description or ""),
            extracted_description=description,
        )


def _tracker(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Postulations"
    sheet.append(["Entreprise", "Poste", "Lien offre"])
    sheet.append(["Existing Co", "Data Engineer", "https://example.test/existing"])
    workbook.save(path)
    workbook.close()
    return path


def test_discovery_prompt_is_candidate_owned_and_leaves_deduplication_to_studio() -> None:
    root = Path(__file__).resolve().parents[1]
    repository = CandidateProfileRepository(root / "config" / "profiles")
    alex = repository.load_snapshot(root / "config/profiles/example/profile.yaml")
    jamie = repository.load_snapshot(root / "config/profiles/example-b/profile.yaml")
    tracker = root / "tracker-to-attach.xlsx"

    alex_prompt = build_discovery_prompt(
        CandidateContext.from_snapshot(alex), tracker_path=tracker, requested_count=7
    )
    jamie_prompt = build_discovery_prompt(
        CandidateContext.from_snapshot(jamie), tracker_path=tracker, requested_count=7
    )

    assert "return up to 21" in alex_prompt
    assert str(tracker) in alex_prompt
    assert "authoritative cross-campaign deduplication locally" in alex_prompt
    assert "strict JSON only" in alex_prompt
    assert "Airflow" in alex_prompt
    assert "PyTorch" in jamie_prompt
    assert "Engineering degree, Data major" in alex_prompt
    assert "mandatory credential absent from the supplied candidate evidence" in alex_prompt
    assert "semantic_fit_score" in alex_prompt
    assert "semantic_fit_rationale" in alex_prompt
    assert "central day-to-day function" in alex_prompt
    assert alex_prompt != jamie_prompt
    assert "PrivateEmployer" not in alex_prompt + jamie_prompt
    assert "Wavestone" not in alex_prompt + jamie_prompt


def test_discovery_prompt_includes_non_it_evidence_and_custom_cv_sections() -> None:
    payload = {
        "availability": "One month notice",
        "locale": "en-GB",
        "baseline_cv": {
            "experience": [
                {
                    "title": "Regulatory Affairs Associate",
                    "dates": "2023-2026",
                    "bullets": ["Prepared EU MDR technical documentation."],
                }
            ],
            "education": [],
        },
        "skill_policy": {"verified": {}, "transferable": {}},
        "projects": [],
        "facts": [
            {
                "claim": "Coordinated notified-body submissions.",
                "keywords": ["EU MDR"],
                "role_tags": ["regulatory affairs"],
            }
        ],
        "additional_evidence_blocks": [
            {
                "label": "Certifications & Training",
                "latex": "ISO 13485 Internal Auditor",
            }
        ],
        "search_preferences": {},
        "work_authorization": "European Union",
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    context = CandidateContext(_serialized=serialized, context_hash="a" * 64)

    prompt = build_discovery_prompt(
        context,
        tracker_path=Path("tracker.xlsx"),
        requested_count=3,
    )

    assert "Prepared EU MDR technical documentation" in prompt
    assert "Coordinated notified-body submissions" in prompt
    assert "Certifications & Training" in prompt
    assert "ISO 13485 Internal Auditor" in prompt


def test_discovery_import_creates_campaign_with_the_same_candidate_and_tracker(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    profiles = root / "config" / "profiles"
    repository = CandidateProfileRepository(profiles)
    campaign_service = FakeCampaignService()
    service = DiscoveryHandoffService(
        repository=repository,
        campaign_service=campaign_service,
        store=DiscoveryHandoffStore(tmp_path / "discoveries"),
    )
    tracker = _tracker(tmp_path / "tracker.xlsx")
    profile_path = profiles / "example" / "profile.yaml"

    record = service.prepare(
        profile_path=profile_path,
        tracker_path=tracker,
        requested_count=5,
        conversation_url="https://chatgpt.com/c/example",
    )
    assert record.status == "prepared"
    assert record.prompt_path.is_file()
    assert record.conversation_url is not None

    offers = [
        {
            "company": "Fresh Co",
            "role": "Data Engineer",
            "url": "https://example.test/fresh",
            "description": "A complete and authoritative offer description for later ATS analysis.",
        }
    ]
    updated, campaign = service.import_candidates(record.discovery_id, candidates=offers)

    assert updated.status == "campaign_created"
    assert updated.campaign_id == campaign.campaign_id
    assert campaign_service.calls == [
        {
            "profile_path": profile_path.resolve(),
            "tracker_path": tracker.resolve(),
            "candidates": offers,
            "limit": 5,
        }
    ]
    assert (record.discovery_dir / "candidates.json").is_file()


def test_interrupted_discovery_can_resume_from_its_persisted_prompt(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    profiles = root / "config" / "profiles"
    store = DiscoveryHandoffStore(tmp_path / "discoveries")
    offer = DiscoveryOffer(
        company="Resume Co",
        role="Data Engineer",
        url="https://example.test/resume",
        description=(
            "Build reliable data pipelines, automated quality controls and monitored "
            "production analytics while collaborating with finance and product teams."
        ),
        semantic_fit_score=88,
        semantic_fit_rationale="The role matches the configured data engineering evidence.",
    )
    service = DiscoveryHandoffService(
        repository=CandidateProfileRepository(profiles),
        campaign_service=FakeCampaignService(),
        store=store,
        agent_factory=lambda _callback: FakeDiscoveryAgent(
            DiscoveryBatch(offers=[offer], END_OF_RESPONSE=True)
        ),
    )
    record = service.prepare(
        profile_path=profiles / "example" / "profile.yaml",
        tracker_path=_tracker(tmp_path / "tracker.xlsx"),
        requested_count=1,
    )
    store.save(record.model_copy(update={"status": "running", "phase": "searching_web"}))

    resumable = service.retry_interrupted(record.discovery_id)
    completed, campaign = service.execute(record.discovery_id)

    assert resumable.status == "ready_for_codex"
    assert resumable.events[-1]["status"] == "resuming_after_interruption"
    assert completed.status == "campaign_created"
    assert campaign.campaign_id == "campaign-12345678"


def test_prepared_discovery_can_resume_after_a_studio_restart(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    profiles = root / "config" / "profiles"
    service = DiscoveryHandoffService(
        repository=CandidateProfileRepository(profiles),
        campaign_service=FakeCampaignService(),
        store=DiscoveryHandoffStore(tmp_path / "discoveries"),
    )
    record = service.prepare(
        profile_path=profiles / "example" / "profile.yaml",
        tracker_path=_tracker(tmp_path / "tracker.xlsx"),
        requested_count=1,
    )

    resumed = service.retry_interrupted(record.discovery_id)

    assert resumed.status == "ready_for_codex"
    assert resumed.phase == "prepared"


def test_discovery_can_be_cancelled_before_it_creates_a_campaign(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    profiles = root / "config" / "profiles"
    service = DiscoveryHandoffService(
        repository=CandidateProfileRepository(profiles),
        campaign_service=FakeCampaignService(),
        store=DiscoveryHandoffStore(tmp_path / "discoveries"),
    )
    record = service.prepare(
        profile_path=profiles / "example" / "profile.yaml",
        tracker_path=_tracker(tmp_path / "tracker.xlsx"),
        requested_count=1,
    )

    cancelled = service.request_cancel(record.discovery_id)

    assert cancelled.status == "cancelled"
    assert cancelled.cancel_requested_at is not None
    assert cancelled.cancelled_at is not None
    with pytest.raises(DiscoveryCancelled):
        service.import_candidates(
            record.discovery_id,
            candidates=[
                {
                    "company": "Never Imported",
                    "role": "Legal Counsel",
                    "url": "https://example.test/never-imported",
                    "description": "A sufficiently complete legal offer that must not be imported.",
                }
            ],
        )


def test_discovery_execute_filters_terminal_urls_and_preserves_unknowns(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    profiles = root / "config" / "profiles"
    repository = CandidateProfileRepository(profiles)
    campaign_service = FakeCampaignService()
    offers = [
        DiscoveryOffer(
            company=company,
            role="Regulatory Affairs Specialist",
            url=url,
            description=(
                "Prepare technical documentation, coordinate submissions and maintain "
                "quality-system evidence for regulated products and cross-functional teams."
            ),
            semantic_fit_score=85,
            semantic_fit_rationale=(
                "The role directly matches the configured regulatory responsibilities."
            ),
        )
        for company, url in (
            ("Active Co", "https://example.test/active"),
            ("Gone Co", "https://example.test/gone"),
            ("Protected Co", "https://example.test/protected"),
        )
    ]
    batch = DiscoveryBatch(offers=offers, END_OF_RESPONSE=True)
    statuses = {
        "https://example.test/active": "available",
        "https://example.test/gone": "unavailable",
        "https://example.test/protected": "unknown",
    }
    service = DiscoveryHandoffService(
        repository=repository,
        campaign_service=campaign_service,
        store=DiscoveryHandoffStore(tmp_path / "discoveries"),
        agent_factory=lambda _callback: FakeDiscoveryAgent(batch),
        availability_verifier=FakeAvailabilityVerifier(statuses),
    )
    record = service.prepare(
        profile_path=profiles / "example" / "profile.yaml",
        tracker_path=_tracker(tmp_path / "tracker.xlsx"),
        requested_count=2,
    )

    updated, _campaign = service.execute(record.discovery_id)

    imported = campaign_service.calls[0]["candidates"]
    assert [candidate["company"] for candidate in imported] == ["Active Co", "Protected Co"]
    assert [candidate["source"] for candidate in imported] == [
        "codex_web_claimed_primary_http_available",
        "codex_web_claimed_primary_http_unverified",
    ]
    assert updated.candidate_count == 2
    assert (record.discovery_dir / "agent-result.json").is_file()
    checks = json.loads((record.discovery_dir / "availability.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in checks["checks"]] == [
        "available",
        "unavailable",
        "unknown",
    ]
    assert len(updated.events) == 3


def test_http_availability_verifier_distinguishes_terminal_and_anti_bot_responses() -> None:
    def response(status_code: int):
        return lambda url: httpx.Response(
            status_code,
            request=httpx.Request("GET", url),
        )

    assert (
        HttpOfferAvailabilityVerifier(response(200)).verify("https://example.test/open").status
        == "available"
    )
    assert (
        HttpOfferAvailabilityVerifier(response(410)).verify("https://example.test/gone").status
        == "unavailable"
    )
    assert (
        HttpOfferAvailabilityVerifier(response(403)).verify("https://example.test/protected").status
        == "unknown"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Cette offre n’est plus disponible.",
        "This position has been filled.",
        "Esta oferta ya no está disponible.",
    ],
)
def test_http_availability_verifier_rejects_closed_offer_markers_on_http_200(
    message: str,
) -> None:
    description = " ".join(["Detailed role responsibilities and qualifications."] * 12)
    html = f"<html><body><header>{message}</header><main>{description}</main></body></html>"

    def response(url: str) -> httpx.Response:
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    result = HttpOfferAvailabilityVerifier(response).verify("https://example.test/jobs/role")

    assert result.status == "unavailable"
    assert "position is closed" in result.reason


def test_http_availability_verifier_ignores_closed_words_inside_page_scripts() -> None:
    description = " ".join(["Detailed role responsibilities and qualifications."] * 12)
    html = (
        "<html><head><script>const closed = 'This job is no longer available';</script></head>"
        f"<body><main>{description}</main></body></html>"
    )

    def response(url: str) -> httpx.Response:
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    result = HttpOfferAvailabilityVerifier(response).verify("https://example.test/jobs/role")

    assert result.status == "available"


def test_http_availability_verifier_rejects_job_redirected_to_same_site_homepage() -> None:
    def redirected(_url: str) -> httpx.Response:
        return httpx.Response(
            200,
            request=httpx.Request("GET", "https://example.test/"),
        )

    result = HttpOfferAvailabilityVerifier(redirected).verify("https://example.test/jobs/role-123")

    assert result.status == "unavailable"
    assert result.final_url == "https://example.test/"


def test_http_verifier_extracts_authoritative_jobposting_json_ld() -> None:
    authoritative = (
        "Lead exhibition delivery from production brief through opening. "
        "Coordinate artists, curators, venues, suppliers, budgets, artwork logistics, "
        "installation schedules, accessibility and post-event evaluation across a "
        "touring cultural programme with several public partners."
    )
    html = f"""
    <html><head><script type="application/ld+json">
    {{"@context":"https://schema.org","@type":"JobPosting",
      "title":"Exhibition Producer","description":"<p>{authoritative}</p>"}}
    </script></head><body>Generic careers landing page.</body></html>
    """

    def response(url: str) -> httpx.Response:
        return httpx.Response(200, text=html, request=httpx.Request("GET", url))

    result = HttpOfferAvailabilityVerifier(response).verify("https://example.test/jobs/producer")

    assert result.status == "available"
    assert result.content_status == "verified"
    assert result.extracted_description == authoritative
    assert result.content_characters == len(authoritative)
    assert result.content_sha256 is not None
    assert "extracted_description" not in result.model_dump(mode="json")


def test_discovery_replaces_agent_summary_with_verified_page_content(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    profiles = root / "config" / "profiles"
    repository = CandidateProfileRepository(profiles)
    campaign_service = FakeCampaignService()
    url = "https://example.test/verified-role"
    agent_summary = (
        "This model-written summary is deliberately different from the employer page and "
        "must never become the ATS source when authoritative page content was extracted."
    )
    authoritative = (
        "Build accessible React and TypeScript interfaces from product requirements, "
        "maintain a reusable design system, write automated browser tests with Playwright, "
        "measure Web Vitals, review analytics and collaborate with product designers and "
        "backend engineers throughout delivery."
    )
    offer = DiscoveryOffer(
        company="Verified Co",
        role="Frontend Software Engineer",
        url=url,
        description=agent_summary,
        semantic_fit_score=90,
        semantic_fit_rationale="The role matches the candidate's verified frontend experience.",
    )
    service = DiscoveryHandoffService(
        repository=repository,
        campaign_service=campaign_service,
        store=DiscoveryHandoffStore(tmp_path / "discoveries"),
        agent_factory=lambda _callback: FakeDiscoveryAgent(
            DiscoveryBatch(offers=[offer], END_OF_RESPONSE=True)
        ),
        availability_verifier=FakeAvailabilityVerifier(
            {url: "available"},
            {url: authoritative},
        ),
    )
    record = service.prepare(
        profile_path=profiles / "example" / "profile.yaml",
        tracker_path=_tracker(tmp_path / "tracker.xlsx"),
        requested_count=1,
    )

    service.execute(record.discovery_id)

    imported = campaign_service.calls[0]["candidates"][0]
    assert imported["description"] == authoritative
    assert imported["source"] == SOURCE_HTTP_CONTENT_VERIFIED
    checks = json.loads((record.discovery_dir / "availability.json").read_text("utf-8"))
    assert checks["checks"][0]["content_status"] == "verified"
    assert checks["checks"][0]["content_characters"] == len(authoritative)
    assert checks["checks"][0]["content_sha256"] is not None
    assert "extracted_description" not in checks["checks"][0]
