from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RunStatus = Literal["pending", "running", "completed", "blocked", "failed"]


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=8, max_length=160)
    candidate_id: str = Field(min_length=2, max_length=80)
    profile_path: Path
    status: RunStatus
    current_phase: str = Field(min_length=1, max_length=80)
    phase_history: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    offer_url: str | None = None
    offer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_dir: Path
    artifacts: dict[str, dict[str, object]] = Field(default_factory=dict)
    review: dict[str, object] | None = None
    blockers: list[str] = Field(default_factory=list)
    agent_events: list[dict[str, object]] = Field(default_factory=list)


class RunStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._record_path_cache: dict[str, Path] = {}

    def create(self, record: RunRecord) -> RunRecord:
        run_dir = self._record_dir(record.candidate_id, record.run_id)
        run_dir.mkdir(parents=True, exist_ok=False)
        self.save(record)
        return record

    def save(self, record: RunRecord) -> RunRecord:
        path = self._record_path(record.candidate_id, record.run_id)
        if not path.parent.is_dir():
            raise FileNotFoundError(f"run directory does not exist: {path.parent}")
        _atomic_write_json(path, record.model_dump(mode="json"))
        self._record_path_cache[record.run_id] = path
        return record

    def get(self, run_id: str) -> RunRecord:
        cached = self._record_path_cache.get(run_id)
        if cached is not None and cached.is_file():
            return RunRecord.model_validate_json(cached.read_text(encoding="utf-8"))
        matches = list(self.root.glob(f"*/{run_id}/run.json"))
        if len(matches) != 1:
            raise FileNotFoundError(f"application run not found: {run_id}")
        self._record_path_cache[run_id] = matches[0]
        return RunRecord.model_validate_json(matches[0].read_text(encoding="utf-8"))

    def list_for_candidate(self, candidate_id: str, *, limit: int = 100) -> list[RunRecord]:
        records: list[RunRecord] = []
        for path in self.root.glob(f"{candidate_id}/*/run.json"):
            try:
                record = RunRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            self._record_path_cache[record.run_id] = path
            records.append(record)
        return sorted(records, key=lambda record: record.updated_at, reverse=True)[:limit]

    def transition(
        self,
        record: RunRecord,
        *,
        status: RunStatus | None = None,
        phase: str | None = None,
        artifacts: dict[str, dict[str, object]] | None = None,
        review: dict[str, object] | None = None,
        blockers: list[str] | None = None,
        agent_events: list[dict[str, object]] | None = None,
    ) -> RunRecord:
        history = list(record.phase_history)
        if phase is not None and (not history or history[-1] != phase):
            history.append(phase)
        updated = record.model_copy(
            update={
                "status": status or record.status,
                "current_phase": phase or record.current_phase,
                "phase_history": history,
                "updated_at": utc_now(),
                "artifacts": artifacts if artifacts is not None else record.artifacts,
                "review": review if review is not None else record.review,
                "blockers": blockers if blockers is not None else record.blockers,
                "agent_events": (agent_events if agent_events is not None else record.agent_events),
            }
        )
        return self.save(updated)

    def _record_dir(self, candidate_id: str, run_id: str) -> Path:
        return self.root / candidate_id / run_id

    def _record_path(self, candidate_id: str, run_id: str) -> Path:
        return self._record_dir(candidate_id, run_id) / "run.json"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
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
