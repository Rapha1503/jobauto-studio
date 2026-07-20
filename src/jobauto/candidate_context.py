from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from jobauto.candidate_snapshot import CandidateSnapshot


@dataclass(frozen=True)
class ContextCapsule:
    root: Path
    manifest_path: Path
    context_hash: str
    asset_allowlist: tuple[str, ...]


class ContextPurpose(StrEnum):
    STRATEGY = "strategy"
    CV_WRITER = "cv_writer"
    CV_LATEX_WRITER = "cv_latex_writer"
    LETTER_WRITER = "letter_writer"
    SUPERVISOR = "supervisor"


@dataclass(frozen=True)
class CandidateContextView:
    purpose: ContextPurpose
    _serialized: str
    parent_context_hash: str
    view_hash: str

    @property
    def serialized(self) -> str:
        return self._serialized

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self._serialized)


@dataclass(frozen=True)
class CandidateContext:
    _serialized: str
    context_hash: str

    @classmethod
    def from_snapshot(cls, snapshot: CandidateSnapshot) -> CandidateContext:
        profile = snapshot.profile
        identity = profile.identity
        payload = {
            "adaptation_policy": snapshot.adaptation_policy.model_dump(mode="json"),
            "additional_evidence_blocks": _additional_evidence_blocks(snapshot),
            "asset_hashes": dict(sorted(snapshot.asset_hashes.items())),
            "availability": profile.availability,
            "baseline_cv": snapshot.cv_source.model_dump(mode="json"),
            "candidate_id": profile.candidate_id,
            "facts": [
                fact.model_dump(mode="json")
                for fact in sorted(snapshot.facts.facts, key=lambda fact: fact.fact_id)
                if fact.status.value == "verified"
            ],
            "unapproved_fact_ids": sorted(
                fact.fact_id for fact in snapshot.facts.facts if fact.status.value != "verified"
            ),
            "evidence_ids": sorted(snapshot.evidence_ids),
            "forbidden_claims": sorted(profile.forbidden_claims),
            "identity": {
                "email": identity.email.strip().casefold(),
                "first_name": _compact(identity.first_name),
                "last_name": _compact(identity.last_name),
                "location": _compact(identity.location),
                "phone": _compact(identity.phone),
            },
            "locale": profile.locale,
            "letter_reference": snapshot.letter_reference,
            "projects": [
                entry.model_dump(mode="json")
                for entry in sorted(snapshot.project_bank.entries, key=lambda entry: entry.id)
            ],
            "project_lab": profile.project_lab.model_dump(mode="json"),
            "protected_claims": sorted(profile.protected_claims),
            "search_preferences": snapshot.search_preferences.model_dump(mode="json"),
            "source_preserving_blocks": _source_preserving_blocks(snapshot),
            "skill_policy": snapshot.skill_policy.context_data(),
            "snapshot_hash": snapshot.snapshot_hash,
            "work_authorization": profile.work_authorization,
        }
        serialized = _canonical_json(payload)
        return cls(
            _serialized=serialized,
            context_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    @property
    def serialized(self) -> str:
        return self._serialized

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self._serialized)

    def prompt_view(self, purpose: ContextPurpose) -> CandidateContextView:
        payload = self.payload
        selected = {key: payload[key] for key in _PROMPT_VIEW_KEYS[purpose] if key in payload}
        serialized = _canonical_json(selected)
        return CandidateContextView(
            purpose=purpose,
            _serialized=serialized,
            parent_context_hash=self.context_hash,
            view_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def write_context_capsule(self, root: Path) -> ContextCapsule:
        capsule_root = root.expanduser().resolve()
        if capsule_root.exists():
            raise ValueError(f"context capsule root already exists: {capsule_root}")
        capsule_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(
            tempfile.mkdtemp(
                dir=capsule_root.parent,
                prefix=f".{capsule_root.name}.",
            )
        )

        asset_allowlist = tuple(sorted(self.payload["asset_hashes"]))
        manifest_path = capsule_root / "context_manifest.json"
        try:
            _atomic_write(temporary_root / "candidate_context.json", self._serialized)
            _atomic_write(
                temporary_root / manifest_path.name,
                _canonical_json(
                    {
                        "asset_allowlist": list(asset_allowlist),
                        "context_hash": self.context_hash,
                    }
                ),
            )
            os.replace(temporary_root, capsule_root)
        except BaseException:
            shutil.rmtree(temporary_root, ignore_errors=True)
            raise
        return ContextCapsule(
            root=capsule_root,
            manifest_path=manifest_path,
            context_hash=self.context_hash,
            asset_allowlist=asset_allowlist,
        )


def _compact(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split())


_PROMPT_VIEW_KEYS: dict[ContextPurpose, tuple[str, ...]] = {
    ContextPurpose.STRATEGY: (
        "additional_evidence_blocks",
        "adaptation_policy",
        "availability",
        "baseline_cv",
        "candidate_id",
        "evidence_ids",
        "facts",
        "forbidden_claims",
        "locale",
        "project_lab",
        "projects",
        "protected_claims",
        "skill_policy",
        "unapproved_fact_ids",
        "work_authorization",
    ),
    ContextPurpose.CV_WRITER: (
        "additional_evidence_blocks",
        "adaptation_policy",
        "availability",
        "baseline_cv",
        "candidate_id",
        "evidence_ids",
        "facts",
        "forbidden_claims",
        "identity",
        "locale",
        "project_lab",
        "projects",
        "protected_claims",
        "skill_policy",
        "unapproved_fact_ids",
        "work_authorization",
    ),
    ContextPurpose.CV_LATEX_WRITER: (
        "adaptation_policy",
        "candidate_id",
        "forbidden_claims",
        "identity",
        "locale",
        "protected_claims",
    ),
    ContextPurpose.LETTER_WRITER: (
        "additional_evidence_blocks",
        "adaptation_policy",
        "availability",
        "candidate_id",
        "evidence_ids",
        "facts",
        "forbidden_claims",
        "identity",
        "letter_reference",
        "locale",
        "projects",
        "protected_claims",
        "skill_policy",
        "unapproved_fact_ids",
        "work_authorization",
    ),
    ContextPurpose.SUPERVISOR: (
        "additional_evidence_blocks",
        "adaptation_policy",
        "availability",
        "baseline_cv",
        "candidate_id",
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
    ),
}


def _source_preserving_blocks(snapshot: CandidateSnapshot) -> list[dict[str, object]]:
    mapping = snapshot.cv_mapping
    if mapping is None:
        return []
    source = snapshot.cv_template_bytes
    return [
        {
            "block_id": block.block_id,
            "kind": block.kind.value,
            "fidelity": block.policy.fidelity.value,
            "required": block.policy.required,
            "latex": source[block.start_byte : block.end_byte].decode("utf-8"),
        }
        for block in mapping.blocks
    ]


def _additional_evidence_blocks(snapshot: CandidateSnapshot) -> list[dict[str, object]]:
    mapping = snapshot.cv_mapping
    if mapping is None:
        return []
    source = snapshot.cv_template_bytes
    return [
        {
            "evidence_id": f"source_block.{block.block_id}",
            "block_id": block.block_id,
            "label": block.label,
            "fidelity": block.policy.fidelity.value,
            "latex": source[block.start_byte : block.end_byte].decode("utf-8"),
        }
        for block in mapping.blocks
        if block.kind.value != "identity"
    ]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
