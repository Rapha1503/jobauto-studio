from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jobauto.discovery_handoff import OfferAvailabilityVerifier
from jobauto.run_store import utc_now
from jobauto.studio_campaign import StudioCampaignService
from jobauto.submission_handoff import (
    HandoffStatus,
    SubmissionHandoffRecord,
    SubmissionHandoffService,
)
from jobauto.submission_preferences import SubmissionMode

SubmissionCampaignStatus = Literal[
    "waiting_for_documents",
    "ready_for_chrome",
    "in_progress",
    "partial",
    "dry_run",
    "submitted",
    "blocked",
]
_OFFER_UNAVAILABLE_PREFIX = "offer_unavailable_before_submission:"


class SubmissionCampaignItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str
    company: str
    role: str
    handoff_id: str | None = None
    status: HandoffStatus | Literal["waiting_for_documents"]
    blockers: list[str] = Field(default_factory=list)


class SubmissionCampaignSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    campaign_id: str
    candidate_id: str
    mode: SubmissionMode
    status: SubmissionCampaignStatus
    items: list[SubmissionCampaignItem]
    ready_count: int = Field(ge=0)
    claimed_count: int = Field(ge=0)
    submitted_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    waiting_count: int = Field(ge=0)


class SubmissionCampaignService:
    """Build an idempotent Chrome handoff queue from completed campaign runs."""

    def __init__(
        self,
        *,
        campaign_service: StudioCampaignService,
        handoff_service: SubmissionHandoffService,
        availability_verifier: OfferAvailabilityVerifier | None = None,
    ) -> None:
        self.campaign_service = campaign_service
        self.handoff_service = handoff_service
        self.availability_verifier = availability_verifier

    def prepare(
        self,
        campaign_id: str,
        *,
        mode: SubmissionMode | None = None,
    ) -> SubmissionCampaignSummary:
        campaign = self.campaign_service.get(campaign_id)
        existing = self.handoff_service.store.list_for_campaign(campaign_id)
        if mode is not None:
            incompatible = [
                record
                for record in existing
                if record.preferences.mode is not mode
                and record.status in {"claimed_for_chrome", "submitted"}
            ]
            if incompatible:
                raise ValueError(
                    "submission mode cannot change after Chrome claimed a campaign packet"
                )
            for record in existing:
                self.handoff_service.set_mode(record.handoff_id, mode)
            existing = self.handoff_service.store.list_for_campaign(campaign_id)
        frozen_preferences = existing[0].preferences if existing else None
        if frozen_preferences is None and mode is not None:
            snapshot = self.handoff_service.repository.load_snapshot(campaign.profile_path)
            frozen_preferences = snapshot.submission_preferences.model_copy(update={"mode": mode})
        for item in campaign.items:
            if (
                item.decision == "selected"
                and item.run_id is not None
                and item.run_status == "completed"
            ):
                handoff = self.handoff_service.prepare(
                    campaign_id,
                    item.run_id,
                    preferences=frozen_preferences,
                )
                handoff = self._refresh_offer_availability(handoff)
                frozen_preferences = handoff.preferences
        return self.get(campaign_id)

    def _refresh_offer_availability(
        self,
        record: SubmissionHandoffRecord,
    ) -> SubmissionHandoffRecord:
        if self.availability_verifier is None or record.status in {
            "claimed_for_chrome",
            "submitted",
        }:
            return record
        check = self.availability_verifier.verify(record.offer_url)
        previous = [
            blocker
            for blocker in record.blockers
            if not blocker.startswith(_OFFER_UNAVAILABLE_PREFIX)
        ]
        blockers = list(previous)
        if check.status == "unavailable":
            blockers.append(f"{_OFFER_UNAVAILABLE_PREFIX} {check.reason}")
        elif check.status == "unknown":
            return record

        if blockers:
            status: HandoffStatus = "blocked"
        elif record.preferences.mode is SubmissionMode.DRY_RUN:
            status = "dry_run"
        elif record.status == "sandbox_verified":
            status = "sandbox_verified"
        else:
            status = "ready_for_chrome"
        if blockers == record.blockers and status == record.status:
            return record
        return self.handoff_service.store.save(
            record.model_copy(
                update={
                    "blockers": blockers,
                    "status": status,
                    "updated_at": utc_now(),
                }
            )
        )

    def claim_next(
        self,
        campaign_id: str,
    ) -> tuple[SubmissionCampaignSummary, SubmissionHandoffRecord | None]:
        self.prepare(campaign_id)
        record = self.handoff_service.claim_next(campaign_id)
        return self.get(campaign_id), record

    def get(self, campaign_id: str) -> SubmissionCampaignSummary:
        campaign = self.campaign_service.get(campaign_id)
        snapshot = self.handoff_service.repository.load_snapshot(campaign.profile_path)
        handoffs = {
            record.run_id: record
            for record in self.handoff_service.store.list_for_campaign(campaign_id)
        }
        items: list[SubmissionCampaignItem] = []
        for item in campaign.items:
            if item.decision != "selected" or item.run_id is None:
                continue
            handoff = handoffs.get(item.run_id)
            if handoff is None:
                status: HandoffStatus | Literal["waiting_for_documents"] = (
                    "blocked"
                    if item.run_status in {"blocked", "failed"}
                    else "waiting_for_documents"
                )
                items.append(
                    SubmissionCampaignItem(
                        run_id=item.run_id,
                        company=item.offer.company,
                        role=item.offer.role,
                        status=status,
                        blockers=list(item.run_blockers),
                    )
                )
                continue
            items.append(_summary_item(handoff))
        mode = (
            next(iter(handoffs.values())).preferences.mode
            if handoffs
            else snapshot.submission_preferences.mode
        )
        return _summary(
            campaign_id=campaign.campaign_id,
            candidate_id=campaign.candidate_id,
            mode=mode,
            items=items,
        )


def _summary_item(record: SubmissionHandoffRecord) -> SubmissionCampaignItem:
    return SubmissionCampaignItem(
        run_id=record.run_id,
        company=record.company,
        role=record.role,
        handoff_id=record.handoff_id,
        status=record.status,
        blockers=list(record.blockers),
    )


def _summary(
    *,
    campaign_id: str,
    candidate_id: str,
    mode: SubmissionMode,
    items: list[SubmissionCampaignItem],
) -> SubmissionCampaignSummary:
    ready_count = sum(item.status in {"ready_for_chrome", "sandbox_verified"} for item in items)
    claimed_count = sum(item.status == "claimed_for_chrome" for item in items)
    submitted_count = sum(item.status == "submitted" for item in items)
    blocked_count = sum(item.status == "blocked" for item in items)
    waiting_count = sum(item.status == "waiting_for_documents" for item in items)
    dry_count = sum(item.status == "dry_run" for item in items)
    if claimed_count and not (
        ready_count or submitted_count or blocked_count or dry_count or waiting_count
    ):
        status: SubmissionCampaignStatus = "in_progress"
    elif waiting_count and not (
        ready_count or claimed_count or submitted_count or blocked_count or dry_count
    ):
        status: SubmissionCampaignStatus = "waiting_for_documents"
    elif (ready_count or claimed_count) and (
        blocked_count or waiting_count or ready_count and claimed_count
    ):
        status = "partial"
    elif ready_count:
        status = "ready_for_chrome"
    elif submitted_count == len(items) and items:
        status = "submitted"
    elif dry_count == len(items) and items:
        status = "dry_run"
    elif blocked_count == len(items) and items:
        status = "blocked"
    elif submitted_count or dry_count or blocked_count:
        status = "partial"
    else:
        status = "waiting_for_documents"
    return SubmissionCampaignSummary(
        campaign_id=campaign_id,
        candidate_id=candidate_id,
        mode=mode,
        status=status,
        items=items,
        ready_count=ready_count,
        claimed_count=claimed_count,
        submitted_count=submitted_count,
        blocked_count=blocked_count,
        waiting_count=waiting_count,
    )
