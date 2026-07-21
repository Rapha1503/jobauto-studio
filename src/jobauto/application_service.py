from __future__ import annotations

import hashlib
import inspect
import json
import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jobauto.artifact_naming import approved_artifact_stem
from jobauto.candidate_context import CandidateContext
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.document_patch import CandidateDocumentDraft
from jobauto.document_renderer import DocumentRenderer, RenderedDocument
from jobauto.models import ApplicationRow
from jobauto.run_store import RunRecord, RunStore, utc_now
from jobauto.text_encoding import repair_utf8_mojibake


class CandidatePipeline(Protocol):
    def generate_candidate_documents(
        self,
        row: ApplicationRow,
        offer_text: str,
        *,
        project_lab_context: str = "",
    ) -> CandidateDocumentDraft: ...

    def review_candidate_documents(
        self,
        row: ApplicationRow,
        package: CandidateDocumentDraft,
        cv_rendered: RenderedDocument,
        letter_rendered: RenderedDocument,
        offer_text: str,
        *,
        block_on_improvable_gap: bool = True,
    ): ...

    def repair_candidate_documents(
        self,
        row: ApplicationRow,
        package: CandidateDocumentDraft,
        review,
        offer_text: str,
    ) -> CandidateDocumentDraft: ...


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_path: Path
    offer_text: str = Field(min_length=20)
    offer_url: str | None = None
    company: str = Field(default="Target company", min_length=1, max_length=200)
    role: str = Field(default="Target role", min_length=1, max_length=200)
    max_repairs: int = Field(default=2, ge=0, le=3)

    @field_validator("offer_text", mode="before")
    @classmethod
    def repair_mojibake_offer_text(cls, value):
        if isinstance(value, str):
            return repair_utf8_mojibake(value)
        return value


PipelineFactory = Callable[..., CandidatePipeline]


class RunApplicationService:
    def __init__(
        self,
        *,
        repository: CandidateProfileRepository,
        store: RunStore,
        pipeline_factory: PipelineFactory,
        renderer: DocumentRenderer | None = None,
    ) -> None:
        self.repository = repository
        self.store = store
        self.pipeline_factory = pipeline_factory
        self.renderer = renderer or DocumentRenderer()

    def start(self, request: RunRequest) -> str:
        snapshot = self.repository.load_snapshot(request.profile_path)
        context = CandidateContext.from_snapshot(snapshot)
        run_id = f"{snapshot.profile.candidate_id}-{uuid4().hex[:12]}"
        run_dir = self.store.root / snapshot.profile.candidate_id / run_id
        now = utc_now()
        record = RunRecord(
            run_id=run_id,
            candidate_id=snapshot.profile.candidate_id,
            profile_path=request.profile_path.expanduser().resolve(),
            status="pending",
            current_phase="pending",
            phase_history=["pending"],
            created_at=now,
            updated_at=now,
            offer_url=request.offer_url,
            offer_sha256=hashlib.sha256(request.offer_text.encode("utf-8")).hexdigest(),
            snapshot_hash=snapshot.snapshot_hash,
            context_hash=context.context_hash,
            run_dir=run_dir,
        )
        self.store.create(record)
        source_artifact_dir = run_dir / "source-artifacts"
        source_artifact_dir.mkdir()
        (source_artifact_dir / "cv.tex").write_bytes(snapshot.cv_template_bytes)
        context.write_context_capsule(run_dir / "context")
        (run_dir / "request.json").write_text(
            request.model_dump_json(indent=2),
            encoding="utf-8",
            newline="\n",
        )
        return run_id

    def execute(self, run_id: str) -> RunRecord:
        record = self.store.get(run_id)
        if record.status != "pending":
            raise ValueError(f"application run is not pending: {record.status}")
        request = RunRequest.model_validate_json(
            (record.run_dir / "request.json").read_text(encoding="utf-8")
        )
        try:
            record = self.store.transition(record, status="running", phase="loading_context")
            snapshot = self.repository.load_snapshot(record.profile_path)
            context = CandidateContext.from_snapshot(snapshot)
            if (
                snapshot.snapshot_hash != record.snapshot_hash
                or context.context_hash != record.context_hash
            ):
                raise ValueError("candidate profile changed after the run was created")

            record = self.store.transition(record, phase="generating_documents")
            event_callback = self._agent_event_callback(run_id, record.run_dir)
            factory_parameters = len(inspect.signature(self.pipeline_factory).parameters)
            if factory_parameters >= 4:
                pipeline = self.pipeline_factory(
                    snapshot,
                    context,
                    event_callback,
                    record.run_dir,
                )
            elif factory_parameters >= 3:
                pipeline = self.pipeline_factory(snapshot, context, event_callback)
            else:
                pipeline = self.pipeline_factory(snapshot, context)
            row = ApplicationRow(
                excel_row=1,
                company=request.company,
                role=request.role,
                url=request.offer_url or "local://pasted-offer",
            )
            package = pipeline.generate_candidate_documents(
                row,
                request.offer_text,
            )
            record = self.store.get(run_id)
            _persist_candidate_package(record.run_dir, package, attempt=1)

            for attempt in range(request.max_repairs + 1):
                record = self.store.transition(record, phase="rendering_documents")
                artifact_dir = record.run_dir / "artifacts" / f"attempt-{attempt + 1}"
                try:
                    cv = self.renderer.render_cv(snapshot, package.cv, artifact_dir)
                except (RuntimeError, ValueError) as exc:
                    event_callback(
                        {
                            "model": "local-renderer",
                            "phase": "render_cv",
                            "status": "rejected",
                            "attempt": attempt + 1,
                            "rejection_reason": str(exc),
                        }
                    )
                    record = self.store.get(run_id)
                    if attempt == request.max_repairs or not hasattr(
                        pipeline, "repair_rendering_failure"
                    ):
                        return self.store.transition(
                            record,
                            status="blocked",
                            phase="render_blocked",
                            blockers=[str(exc)],
                        )
                    record = self.store.transition(record, phase="repairing_documents")
                    package = pipeline.repair_rendering_failure(
                        row,
                        package,
                        surface="cv",
                        error=str(exc),
                        offer_text=request.offer_text,
                    )
                    record = self.store.get(run_id)
                    _persist_candidate_package(record.run_dir, package, attempt=attempt + 2)
                    continue
                try:
                    letter = self.renderer.render_letter(snapshot, package.letter, artifact_dir)
                except (RuntimeError, ValueError) as exc:
                    event_callback(
                        {
                            "model": "local-renderer",
                            "phase": "render_letter",
                            "status": "rejected",
                            "attempt": attempt + 1,
                            "rejection_reason": str(exc),
                        }
                    )
                    record = self.store.get(run_id)
                    if attempt == request.max_repairs or not hasattr(
                        pipeline, "repair_rendering_failure"
                    ):
                        return self.store.transition(
                            record,
                            status="blocked",
                            phase="render_blocked",
                            blockers=[str(exc)],
                        )
                    record = self.store.transition(record, phase="repairing_documents")
                    package = pipeline.repair_rendering_failure(
                        row,
                        package,
                        surface="letter",
                        error=str(exc),
                        offer_text=request.offer_text,
                    )
                    record = self.store.get(run_id)
                    _persist_candidate_package(record.run_dir, package, attempt=attempt + 2)
                    continue
                artifacts = {
                    "cv": _artifact_payload(cv),
                    "letter": _artifact_payload(letter),
                }
                record = self.store.transition(
                    record,
                    phase="reviewing_documents",
                    artifacts=artifacts,
                )
                review_parameters = inspect.signature(
                    pipeline.review_candidate_documents
                ).parameters
                review_kwargs = {}
                if "block_on_improvable_gap" in review_parameters:
                    review_kwargs["block_on_improvable_gap"] = attempt < request.max_repairs
                review = pipeline.review_candidate_documents(
                    row,
                    package,
                    cv,
                    letter,
                    request.offer_text,
                    **review_kwargs,
                )
                record = self.store.get(run_id)
                review_payload = review.model_dump(mode="json")
                (record.run_dir / f"review-{attempt + 1}.json").write_text(
                    review.model_dump_json(indent=2),
                    encoding="utf-8",
                    newline="\n",
                )
                if review.approved:
                    artifact_role = (
                        getattr(package.brief, "open_role", None)
                        or getattr(package.brief, "role", None)
                        or request.role
                    )
                    cv = _publish_approved_document(
                        cv,
                        kind="cv",
                        first_name=snapshot.profile.identity.first_name,
                        last_name=snapshot.profile.identity.last_name,
                        role=artifact_role,
                        company=request.company,
                    )
                    letter = _publish_approved_document(
                        letter,
                        kind="letter",
                        first_name=snapshot.profile.identity.first_name,
                        last_name=snapshot.profile.identity.last_name,
                        role=artifact_role,
                        company=request.company,
                    )
                    artifacts = {
                        "cv": _artifact_payload(cv),
                        "letter": _artifact_payload(letter),
                    }
                    return self.store.transition(
                        record,
                        status="completed",
                        phase="completed",
                        artifacts=artifacts,
                        review=review_payload,
                    )
                if attempt == request.max_repairs:
                    return self.store.transition(
                        record,
                        status="blocked",
                        phase="review_blocked",
                        artifacts=artifacts,
                        review=review_payload,
                        blockers=review.blocking_issues,
                    )
                record = self.store.transition(record, phase="repairing_documents")
                package = pipeline.repair_candidate_documents(
                    row,
                    package,
                    review,
                    request.offer_text,
                )
                record = self.store.get(run_id)
                _persist_candidate_package(record.run_dir, package, attempt=attempt + 2)
            raise RuntimeError("unreachable repair loop state")
        except ValueError as exc:
            record = self.store.get(run_id)
            return self.store.transition(
                record,
                status="blocked",
                phase="blocked",
                blockers=[str(exc)],
            )
        except Exception as exc:
            record = self.store.get(run_id)
            return self.store.transition(
                record,
                status="failed",
                phase="failed",
                blockers=[f"{type(exc).__name__}: {exc}"],
            )

    def get(self, run_id: str) -> RunRecord:
        return self._ensure_published_artifacts(self.store.get(run_id))

    def _ensure_published_artifacts(self, record: RunRecord) -> RunRecord:
        if record.status != "completed" or not record.review or not record.review.get("approved"):
            return record
        expected_prefixes = {"cv": "CV_", "letter": "Lettre_"}
        if all(
            kind in record.artifacts
            and Path(str(record.artifacts[kind].get("pdf_path", ""))).name.startswith(prefix)
            for kind, prefix in expected_prefixes.items()
        ):
            return record
        request = RunRequest.model_validate_json(
            (record.run_dir / "request.json").read_text(encoding="utf-8")
        )
        snapshot = self.repository.load_snapshot(record.profile_path)
        role = _persisted_artifact_role(record.run_dir) or request.role
        artifacts = {kind: dict(payload) for kind, payload in record.artifacts.items()}
        changed = False
        for kind in ("cv", "letter"):
            payload = artifacts.get(kind)
            if payload is None or not payload.get("pdf_path"):
                continue
            stem = approved_artifact_stem(
                kind=kind,
                first_name=snapshot.profile.identity.first_name,
                last_name=snapshot.profile.identity.last_name,
                role=role,
                company=request.company,
            )
            pdf_path = Path(str(payload["pdf_path"]))
            published_pdf = pdf_path.with_name(f"{stem}.pdf")
            if pdf_path != published_pdf:
                shutil.copy2(pdf_path, published_pdf)
                payload["pdf_path"] = str(published_pdf)
                changed = True
            source_value = payload.get("source_path")
            if source_value:
                source_path = Path(str(source_value))
                published_source = source_path.with_name(f"{stem}{source_path.suffix}")
                if source_path != published_source:
                    shutil.copy2(source_path, published_source)
                    payload["source_path"] = str(published_source)
                    changed = True
        if not changed:
            return record
        return self.store.transition(record, artifacts=artifacts)

    def _agent_event_callback(self, run_id: str, run_dir: Path):
        def record_event(event: dict[str, object]) -> None:
            _append_jsonl(run_dir / "agent-events.jsonl", event)
            current = self.store.get(run_id)
            phase = str(event.get("phase", "unknown"))
            status = str(event.get("status", "event"))
            events = list(current.agent_events)
            if event.get("pipeline_outcome") is not None:
                for index in range(len(events) - 1, -1, -1):
                    previous = events[index]
                    if _same_agent_call(previous, event):
                        events[index] = {**previous, **event}
                        break
                else:
                    events.append(event)
            else:
                events.append(event)
            self.store.transition(
                current,
                phase=f"agent:{phase}:{status}",
                agent_events=events,
            )

        return record_event


def _same_agent_call(
    previous: dict[str, object],
    current: dict[str, object],
) -> bool:
    if previous.get("call_id") is not None and current.get("call_id") is not None:
        return (
            previous.get("call_id") == current.get("call_id")
            and previous.get("status") == current.get("status")
            and previous.get("attempt") == current.get("attempt")
        )
    keys = ("phase", "status", "attempt", "input_sha256", "output_sha256")
    return all(previous.get(key) == current.get(key) for key in keys)


def _artifact_payload(document: RenderedDocument) -> dict[str, object]:
    return {
        "source_path": str(document.source_path),
        "pdf_path": str(document.pdf_path),
        "page_count": document.page_count,
        "extracted_text_sha256": document.extracted_text_sha256,
        "extracted_text_characters": len(document.extracted_text),
        "pdf_sha256": document.pdf_sha256,
        "layout_metrics": document.layout_metrics,
    }


def _publish_approved_document(
    document: RenderedDocument,
    *,
    kind: str,
    first_name: str,
    last_name: str,
    role: str,
    company: str,
) -> RenderedDocument:
    stem = approved_artifact_stem(
        kind=kind,
        first_name=first_name,
        last_name=last_name,
        role=role,
        company=company,
    )
    source_path = document.source_path.with_name(f"{stem}{document.source_path.suffix}")
    pdf_path = document.pdf_path.with_name(f"{stem}.pdf")
    shutil.copy2(document.source_path, source_path)
    shutil.copy2(document.pdf_path, pdf_path)
    return replace(document, source_path=source_path, pdf_path=pdf_path)


def _persist_candidate_package(
    run_dir: Path,
    package: CandidateDocumentDraft,
    *,
    attempt: int,
) -> None:
    payload = {
        "brief": package.brief.model_dump(mode="json"),
        "cv_patch": package.cv_patch.model_dump(mode="json"),
        "cv_document": package.cv.document.model_dump(mode="json"),
        "cv_provenance": {
            source_id: list(fact_ids) for source_id, fact_ids in package.cv.provenance.items()
        },
        "cv_latex_patch": (
            package.cv.latex_patch.model_dump(mode="json")
            if package.cv.latex_patch is not None
            else None
        ),
        "letter": package.letter.model_dump(mode="json"),
    }
    path = run_dir / f"candidate-package-{attempt}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _persisted_artifact_role(run_dir: Path) -> str | None:
    packages = sorted(
        run_dir.glob("candidate-package-*.json"),
        key=lambda path: int(path.stem.rsplit("-", 1)[-1]),
    )
    if not packages:
        return None
    payload = json.loads(packages[-1].read_text(encoding="utf-8"))
    brief = payload.get("brief") or {}
    return str(brief.get("open_role") or brief.get("role") or "").strip() or None


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
