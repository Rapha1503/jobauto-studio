from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

from jobauto.candidate_context import CandidateContext
from jobauto.models import (
    ApplicationBrief,
    ApplicationRow,
    BaselineCvCoverage,
    CandidateEvidenceAssessment,
    OfferContract,
)

_CONTRACT_FIELDS = tuple(OfferContract.model_fields)
_OFFER_CONTRACT_CACHE_VERSION = 2
_WRITE_LOCK = threading.RLock()


def offer_contract_key(row: ApplicationRow, offer_text: str) -> str:
    """Return a stable key for the complete offer, independent of the candidate."""

    normalized_offer = " ".join(offer_text.split())
    payload = json.dumps(
        {
            "contract_version": _OFFER_CONTRACT_CACHE_VERSION,
            "company": " ".join(row.company.casefold().split()),
            "role": " ".join(row.role.casefold().split()),
            "offer": normalized_offer,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def baseline_coverage_key(
    contract: OfferContract,
    baseline_cv: str,
    expected_language: str,
) -> str:
    payload = json.dumps(
        {
            "contract": contract.model_dump(mode="json"),
            "baseline_cv": baseline_cv,
            "expected_language": expected_language,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_evidence_key(
    contract: OfferContract,
    context: CandidateContext,
) -> str:
    payload = json.dumps(
        {
            "contract": contract.model_dump(mode="json"),
            "candidate_evidence": candidate_evidence_payload(context),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def candidate_evidence_payload(context: CandidateContext) -> dict[str, object]:
    """Return candidate evidence without profile identity or adaptation permissions."""

    source = context.payload
    selected_keys = (
        "additional_evidence_blocks",
        "availability",
        "baseline_cv",
        "evidence_ids",
        "facts",
        "forbidden_claims",
        "identity",
        "locale",
        "projects",
        "protected_claims",
        "skill_policy",
        "source_preserving_blocks",
        "unapproved_fact_ids",
        "work_authorization",
    )
    payload = {key: source[key] for key in selected_keys if key in source}
    for key in ("additional_evidence_blocks", "source_preserving_blocks"):
        blocks = payload.get(key)
        if isinstance(blocks, list):
            payload[key] = [
                {
                    field: value
                    for field, value in block.items()
                    if field not in {"fidelity", "required"}
                }
                for block in blocks
                if isinstance(block, dict)
            ]
    return payload


def validate_offer_contract(contract: OfferContract, offer_text: str) -> None:
    """Reject unsourced or unstable requirement contracts before candidate strategy."""

    normalized_offer = _trace_text(offer_text)
    missing_excerpts: list[str] = []
    missing_ats_terms: list[str] = []
    for requirement in contract.requirements:
        if _trace_text(requirement.source_excerpt) not in normalized_offer:
            missing_excerpts.append(requirement.requirement_id)
        missing_ats_terms.extend(
            f"{requirement.requirement_id}:{term}"
            for term in requirement.ats_terms
            if _trace_text(term) not in normalized_offer
        )
    failures: list[str] = []
    if missing_excerpts:
        failures.append("source_excerpt=" + ", ".join(missing_excerpts))
    if missing_ats_terms:
        failures.append("ats_terms=" + ", ".join(missing_ats_terms))
    if failures:
        raise ValueError(
            "offer contract contains wording absent from the complete offer: " + "; ".join(failures)
        )


def lock_application_brief_to_offer_contract(
    brief: ApplicationBrief,
    contract: OfferContract,
) -> ApplicationBrief:
    """Make candidate strategy unable to mutate the canonical offer reading."""

    return brief.model_copy(
        update={field: getattr(contract, field) for field in _CONTRACT_FIELDS},
    )


class OfferContractStore:
    """Small filesystem cache shared by profiles and adaptation presets."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def load(self, key: str) -> OfferContract | None:
        path = self.root / f"{key}.json"
        if not path.exists():
            return None
        return OfferContract.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, key: str, contract: OfferContract) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{key}.json"
        _atomic_write(path, contract.model_dump_json(indent=2))
        return path

    def load_baseline_coverage(self, key: str) -> BaselineCvCoverage | None:
        path = self.root / "baseline" / f"{key}.json"
        if not path.exists():
            return None
        return BaselineCvCoverage.model_validate_json(path.read_text(encoding="utf-8"))

    def save_baseline_coverage(self, key: str, coverage: BaselineCvCoverage) -> Path:
        directory = self.root / "baseline"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{key}.json"
        _atomic_write(path, coverage.model_dump_json(indent=2))
        return path

    def load_candidate_evidence(self, key: str) -> CandidateEvidenceAssessment | None:
        path = self.root / "candidate-evidence" / f"{key}.json"
        if not path.exists():
            return None
        return CandidateEvidenceAssessment.model_validate_json(path.read_text(encoding="utf-8"))

    def save_candidate_evidence(
        self,
        key: str,
        assessment: CandidateEvidenceAssessment,
    ) -> Path:
        directory = self.root / "candidate-evidence"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{key}.json"
        _atomic_write(path, assessment.model_dump_json(indent=2))
        return path


def _trace_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with _WRITE_LOCK:
            for attempt in range(5):
                try:
                    os.replace(temporary_name, path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.02 * (attempt + 1))
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
