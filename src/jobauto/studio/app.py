from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from urllib.parse import urlencode

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from jobauto.adaptation_policy import AdaptationPolicy, FidelityLevel
from jobauto.application_service import RunApplicationService, RunRequest
from jobauto.build import compile_latex
from jobauto.candidate_draft import (
    CandidateDraft,
    CandidateDraftStore,
    CandidateDraftUpdate,
    DraftJobStatus,
    DraftOrigin,
    DraftStatus,
    update_candidate_draft,
    validate_candidate_draft,
)
from jobauto.candidate_export import export_candidate_draft
from jobauto.candidate_pipeline import CandidatePipeline
from jobauto.candidate_profile import CandidateProfile
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.candidate_workflow import CandidateWorkflowPipeline
from jobauto.codex_client import CodexClient
from jobauto.cv_change_summary import CvChangeSummary, build_cv_change_summary
from jobauto.cv_source import CvSourceDocument
from jobauto.discovery_handoff import (
    DiscoveryHandoffRecord,
    DiscoveryHandoffService,
    DiscoveryHandoffStore,
    HttpOfferAvailabilityVerifier,
)
from jobauto.document_patch import CvDocumentDraft
from jobauto.document_renderer import DocumentRenderer
from jobauto.excel_schema import ensure_tracker_schema
from jobauto.generated_cv_template import generated_cv_template_bytes
from jobauto.latex_cv_source import (
    MAX_TEX_SOURCE_BYTES,
    LatexCvMapping,
    TexBlockCorrection,
    TexBlockKind,
)
from jobauto.models import ApplicationBrief
from jobauto.offer_catalog import OfferCandidate
from jobauto.pdf_preview import render_pdf_first_page
from jobauto.profile_extraction import CandidateProfileExtractor
from jobauto.run_store import RunStore
from jobauto.studio.pdf_imports import MAX_PDF_SOURCE_BYTES, PdfImportStore
from jobauto.studio.tex_imports import TexImportStore
from jobauto.studio_campaign import (
    StudioCampaignRecord,
    StudioCampaignService,
    StudioCampaignStore,
)
from jobauto.submission_campaign import SubmissionCampaignService
from jobauto.submission_handoff import (
    SubmissionHandoffRecord,
    SubmissionHandoffService,
    SubmissionHandoffStore,
    SubmissionReceipt,
)
from jobauto.submission_preferences import SubmissionMode
from jobauto.tracker_io import save_workbook_atomically, tracker_lock


@dataclass(frozen=True)
class StudioProfile:
    profile: CandidateProfile
    source: CvSourceDocument
    policy: AdaptationPolicy
    raw_source: str


class StudioRunRequest(BaseModel):
    model_config = {"extra": "forbid"}

    offer_text: str = Field(min_length=20)
    offer_url: str | None = None
    company: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=200)
    max_repairs: int = Field(default=2, ge=0, le=3)


class StudioCampaignRequest(BaseModel):
    model_config = {"extra": "forbid"}

    tracker_path: Path | None = None
    candidates: list[dict[str, object]] = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class StudioCampaignExpansionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    additional_count: int | None = Field(default=None, ge=1, le=20)


class StudioSubmissionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    mode: SubmissionMode | None = None


class StudioDiscoveryRequest(BaseModel):
    model_config = {"extra": "forbid"}

    tracker_path: Path | None = None
    requested_count: int = Field(default=5, ge=1, le=100)
    conversation_url: str | None = None


class StudioDiscoveryImport(BaseModel):
    model_config = {"extra": "forbid"}

    candidates: list[dict[str, object]] = Field(min_length=1)


class SandboxFilePayload(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=300)
    content_base64: str = Field(min_length=8, max_length=5_000_000)


class SandboxSubmission(BaseModel):
    model_config = {"extra": "forbid"}

    full_name: str = Field(min_length=2, max_length=200)
    email: str = Field(min_length=3, max_length=254)
    location: str = Field(min_length=2, max_length=300)
    message: str = Field(min_length=20, max_length=10_000)
    cv: SandboxFilePayload
    letter: SandboxFilePayload


class TexMappingUpdate(BaseModel):
    model_config = {"extra": "forbid"}

    blocks: list[TexBlockCorrection] = Field(min_length=1)


class ManualDraftCreate(BaseModel):
    model_config = {"extra": "forbid"}

    locale: str = Field(default="en-GB", min_length=2, max_length=20)


def _installed_codex_plugins() -> set[str]:
    codex = shutil.which("codex")
    if codex is None:
        return set()
    try:
        completed = subprocess.run(
            [codex, "plugin", "list", "--json"],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return set()
    return {
        str(item.get("pluginId", ""))
        for item in payload.get("installed", [])
        if item.get("installed") and item.get("enabled")
    }


def _local_requirements() -> tuple[dict[str, str | bool], ...]:
    plugins = _installed_codex_plugins()
    return (
        {
            "name": "Codex CLI",
            "ready": shutil.which("codex") is not None,
            "purpose": "offer discovery and document agents",
        },
        {
            "name": "LaTeX",
            "ready": shutil.which("pdflatex") is not None,
            "purpose": "source preview and final PDFs",
        },
        {
            "name": "Codex Chrome control",
            "ready": "chrome@openai-bundled" in plugins,
            "purpose": "authenticated browser applications",
            "command": "codex plugin add chrome@openai-bundled",
        },
        {
            "name": "JobAuto plugin",
            "ready": "jobauto@jobauto-studio" in plugins,
            "purpose": "consume reviewed application packets in Chrome",
            "command": ("codex plugin marketplace add .\ncodex plugin add jobauto@jobauto-studio"),
        },
    )


class ProfileCatalog:
    def __init__(self, *profile_roots: Path) -> None:
        self.profile_roots = tuple(root.expanduser().resolve() for root in profile_roots)
        for root in self.profile_roots:
            root.mkdir(parents=True, exist_ok=True)
        self.repositories = {root: CandidateProfileRepository(root) for root in self.profile_roots}
        self.repository = self

    def all(self) -> list[StudioProfile]:
        profiles: list[StudioProfile] = []
        for root in self.profile_roots:
            profiles.extend(self.all_from(root))
        return profiles

    def all_from(self, root: Path) -> list[StudioProfile]:
        selected = root.expanduser().resolve()
        repository = self.repositories[selected]
        paths = sorted(
            selected.glob("*/profile.yaml"),
            key=lambda path: (path.stat().st_mtime_ns, str(path)),
        )
        return [self._load(path, repository) for path in paths]

    def get(self, candidate_id: str) -> StudioProfile:
        for item in self.all():
            if item.profile.candidate_id == candidate_id:
                return item
        raise KeyError(candidate_id)

    def load_snapshot(self, path: Path):
        source = path.expanduser().resolve()
        for root, repository in self.repositories.items():
            try:
                source.relative_to(root)
            except ValueError:
                continue
            return repository.load_snapshot(source)
        raise ValueError("candidate profile is outside the Studio profile roots")

    def _load(self, path: Path, repository: CandidateProfileRepository) -> StudioProfile:
        snapshot = repository.load_snapshot(path)
        profile = snapshot.profile
        if profile.cv_source_path is None:
            raise ValueError(f"Studio profile is incomplete: {path}")
        raw_source = profile.cv_source_path.read_text(encoding="utf-8")
        return StudioProfile(
            profile=profile,
            source=snapshot.cv_source,
            policy=snapshot.adaptation_policy,
            raw_source=raw_source,
        )


class ProfileWorkspaceState(BaseModel):
    model_config = {"extra": "forbid"}

    active_candidate_id: str | None = None
    archived_candidate_ids: list[str] = Field(default_factory=list)


class ProfileWorkspaceRegistry:
    """Persist profile selection without moving profile or application artifacts."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def get(self) -> ProfileWorkspaceState:
        with self._lock:
            if not self.path.is_file():
                return ProfileWorkspaceState()
            try:
                return ProfileWorkspaceState.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                return ProfileWorkspaceState()

    def select(self, candidate_id: str) -> ProfileWorkspaceState:
        state = self.get()
        archived = [item for item in state.archived_candidate_ids if item != candidate_id]
        return self._save(
            state.model_copy(
                update={
                    "active_candidate_id": candidate_id,
                    "archived_candidate_ids": archived,
                }
            )
        )

    def archive(self, candidate_id: str) -> ProfileWorkspaceState:
        state = self.get()
        archived = list(dict.fromkeys([*state.archived_candidate_ids, candidate_id]))
        active = None if state.active_candidate_id == candidate_id else state.active_candidate_id
        return self._save(
            state.model_copy(
                update={
                    "active_candidate_id": active,
                    "archived_candidate_ids": archived,
                }
            )
        )

    def restore(self, candidate_id: str) -> ProfileWorkspaceState:
        state = self.get()
        archived = [item for item in state.archived_candidate_ids if item != candidate_id]
        return self._save(state.model_copy(update={"archived_candidate_ids": archived}))

    def _save(self, state: ProfileWorkspaceState) -> ProfileWorkspaceState:
        with self._lock:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                text=True,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(state.model_dump_json(indent=2))
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, self.path)
            except BaseException:
                Path(temporary_name).unlink(missing_ok=True)
                raise
        return state


def create_studio_app(
    *,
    project_root: Path | None = None,
    state_root: Path | None = None,
    profiles_root: Path | None = None,
    application_service: RunApplicationService | None = None,
    campaign_service: StudioCampaignService | None = None,
    discovery_handoff_service: DiscoveryHandoffService | None = None,
    submission_handoff_service: SubmissionHandoffService | None = None,
    submission_campaign_service: SubmissionCampaignService | None = None,
    tex_import_store: TexImportStore | None = None,
    pdf_import_store: PdfImportStore | None = None,
    profile_extractor: CandidateProfileExtractor | None = None,
    discovery_agent_factory: Callable | None = None,
    codex_model: str | None = None,
) -> FastAPI:
    module_root = Path(__file__).resolve().parent
    root = project_root.expanduser().resolve() if project_root is not None else None
    default_profiles = root / "config" / "profiles" if root is not None else None
    packaged_profiles = module_root / "example_profiles"
    source_profiles = module_root.parents[2] / "config" / "profiles"
    packaged_demo = module_root / "demo_evidence"
    source_demo = module_root.parents[2] / "docs" / "demo-evidence" / "20260718-nonit-chrome-batch"
    demo_root = next(
        (candidate for candidate in (packaged_demo, source_demo) if candidate.is_dir()),
        None,
    )
    fallback_profiles = (
        packaged_profiles
        if packaged_profiles.is_dir() and any(packaged_profiles.iterdir())
        else source_profiles
    )
    selected_profiles = (
        profiles_root.expanduser().resolve()
        if profiles_root is not None
        else default_profiles
        if default_profiles is not None and default_profiles.is_dir()
        else fallback_profiles
    )
    state = (
        state_root.expanduser().resolve()
        if state_root is not None
        else root / ".codex_work" / "studio"
        if root is not None
        else Path.home() / ".jobauto" / "studio"
    )
    candidate_profiles = state / "candidate-profiles"
    default_tracker = state / "applications.xlsx"
    _ensure_default_tracker(default_tracker)
    templates = Jinja2Templates(directory=module_root / "templates")
    catalog = ProfileCatalog(selected_profiles, candidate_profiles)
    profile_registry = ProfileWorkspaceRegistry(state / "profile-workspace.json")
    service = application_service
    campaigns = campaign_service
    discoveries = discovery_handoff_service
    handoffs = submission_handoff_service
    submission_campaigns = submission_campaign_service
    imports = tex_import_store or TexImportStore(state / "tex-imports")
    pdf_imports = pdf_import_store or PdfImportStore(state / "pdf-imports")
    drafts = CandidateDraftStore(state / "candidate-drafts")
    extraction_service = profile_extractor
    agent_workspace = state / "agent-workspace"
    agent_workspace.mkdir(parents=True, exist_ok=True)
    selected_codex_model = codex_model or os.getenv("JOBAUTO_CODEX_MODEL", "gpt-5.6-sol")
    app = FastAPI(title="JobAuto Studio", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=module_root / "static"), name="static")
    active_discovery_ids: set[str] = set()

    def demo_replay() -> dict[str, object]:
        if demo_root is None:
            raise HTTPException(status_code=404, detail="Demo replay is not installed")
        campaign = json.loads((demo_root / "campaign-summary.json").read_text(encoding="utf-8-sig"))
        manifest = json.loads(
            (demo_root / "artifacts" / "manifest.json").read_text(encoding="utf-8-sig")
        )
        receipts = json.loads((demo_root / "receipts-summary.json").read_text(encoding="utf-8-sig"))
        runs_by_id = {item["run_id"]: item for item in manifest}
        receipts_by_company = {item["company"]: item for item in receipts["receipts"]}
        applications = []
        for offer in campaign["offers"]:
            run = runs_by_id.get(offer.get("run_id"))
            if run is None:
                continue
            applications.append(
                {
                    **offer,
                    "model": run["model"],
                    "final_review": run["final_review"],
                    "agent_trace": run["agent_trace"],
                    "cv": run["cv"],
                    "letter": run["letter"],
                    "cv_url": f"/demo/files/artifacts/{run['cv']['file']}",
                    "letter_url": f"/demo/files/artifacts/{run['letter']['file']}",
                    "review_url": f"/demo/files/artifacts/{run['review_file']}",
                    "trace_url": f"/demo/files/artifacts/{run['agent_trace']['file']}",
                    "receipt": receipts_by_company.get(offer["company"]),
                }
            )
        return {
            "campaign": campaign,
            "applications": applications,
            "receipts": receipts,
            "source_cv_url": (
                "/demo/files/source-cv.pdf" if (demo_root / "source-cv.pdf").is_file() else None
            ),
        }

    def source_draft_url(item) -> str | None:
        source_metadata = item.profile.source_path.parent / "studio_source.json"
        try:
            source_draft = json.loads(source_metadata.read_text(encoding="utf-8"))
            draft_id = str(source_draft.get("draft_id", ""))
            source_draft_record = drafts.get(draft_id) if draft_id else None
            profile_mapping = (
                LatexCvMapping.load(item.profile.cv_mapping_path)
                if item.profile.cv_mapping_path is not None
                else None
            )
            profile_source_hash = (
                hashlib.sha256(item.profile.cv_model_path.read_bytes()).hexdigest()
                if item.profile.cv_model_path is not None
                else None
            )
            source_matches = (
                source_draft_record is not None
                and source_draft_record.origin is not DraftOrigin.LATEX
                and source_draft.get("origin") == source_draft_record.origin.value
                and profile_source_hash == hashlib.sha256(generated_cv_template_bytes()).hexdigest()
                and profile_mapping is None
            ) or (
                source_draft_record is not None
                and source_draft_record.origin is DraftOrigin.LATEX
                and profile_source_hash == source_draft_record.source_sha256
                and profile_mapping is not None
                and profile_mapping.mapping_hash == source_draft_record.mapping_hash
            )
            candidate_version_prefix, version_separator, candidate_version = (
                item.profile.candidate_id.rpartition("-v")
            )
            profile_matches = (
                source_draft_record is not None
                and version_separator == "-v"
                and candidate_version.isdigit()
                and candidate_version_prefix.endswith(f"-{source_draft_record.draft_id[:8]}")
                and source_draft.get("candidate_id", item.profile.candidate_id)
                == item.profile.candidate_id
                and source_draft.get("import_id") == source_draft_record.import_id
            )
            if profile_matches and source_matches:
                return f"/candidate-drafts/{draft_id}"
        except (FileNotFoundError, OSError, ValueError, TypeError):
            pass
        return None

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        workspace_state = profile_registry.get()
        all_user_profiles = _latest_profile_versions(catalog.all_from(candidate_profiles))
        archived_ids = set(workspace_state.archived_candidate_ids)
        user_profiles = [
            item for item in all_user_profiles if item.profile.candidate_id not in archived_ids
        ]
        archived_profiles = [
            item for item in all_user_profiles if item.profile.candidate_id in archived_ids
        ]
        examples = catalog.all_from(selected_profiles)
        active_profile = next(
            (
                item
                for item in user_profiles
                if item.profile.candidate_id == workspace_state.active_candidate_id
            ),
            user_profiles[-1] if user_profiles else None,
        )
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "active_profile": active_profile,
                "active_profile_edit_url": (
                    source_draft_url(active_profile) if active_profile is not None else None
                ),
                "user_profiles": user_profiles,
                "archived_profiles": archived_profiles,
                "example_profiles": examples,
                "codex_model": selected_codex_model,
            },
        )

    @app.get("/demo", response_class=HTMLResponse)
    def checked_demo(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="demo.html",
            context={"demo": demo_replay(), "codex_model": selected_codex_model},
        )

    @app.get("/demo/files/{relative_path:path}")
    def checked_demo_file(relative_path: str) -> FileResponse:
        if demo_root is None:
            raise HTTPException(status_code=404, detail="Demo replay is not installed")
        root_path = demo_root.resolve()
        requested_path = (root_path / relative_path).resolve()
        if not requested_path.is_relative_to(root_path) or not requested_path.is_file():
            raise HTTPException(status_code=404, detail="Demo artifact not found")
        return FileResponse(requested_path)

    @app.post("/profiles/{candidate_id}/select")
    def select_profile(candidate_id: str) -> dict[str, str]:
        _profile_or_404(catalog, candidate_id)
        profile_registry.select(candidate_id)
        return {
            "candidate_id": candidate_id,
            "page_url": f"/profiles/{candidate_id}",
        }

    @app.post("/profiles/{candidate_id}/archive")
    def archive_profile(candidate_id: str) -> dict[str, str]:
        _profile_or_404(catalog, candidate_id)
        profile_registry.archive(candidate_id)
        return {"candidate_id": candidate_id, "page_url": "/"}

    @app.post("/profiles/{candidate_id}/restore")
    def restore_profile(candidate_id: str) -> dict[str, str]:
        _profile_or_404(catalog, candidate_id)
        profile_registry.restore(candidate_id)
        return {"candidate_id": candidate_id, "page_url": "/"}

    @app.get("/setup", response_class=HTMLResponse)
    def setup_workspace(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="setup.html",
            context={"local_requirements": _local_requirements()},
        )

    @app.post("/api/tex-imports", status_code=201)
    async def import_tex_source(request: Request) -> dict[str, str | None]:
        filename = request.headers.get("x-filename", "").strip()
        if not filename:
            raise HTTPException(status_code=422, detail="X-Filename header is required")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_TEX_SOURCE_BYTES:
                raise HTTPException(status_code=413, detail="LaTeX CV exceeds the 2 MB limit")
            chunks.append(chunk)
        source = b"".join(chunks)
        try:
            record = await run_in_threadpool(imports.create, source, filename=filename)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "import_id": record.import_id,
            "page_url": f"/setup/imports/{record.import_id}",
            "compilation_status": record.compilation_status,
            "compilation_error": record.compilation_error,
        }

    @app.post("/api/candidate-drafts/manual", status_code=201)
    def create_manual_candidate_draft(payload: ManualDraftCreate) -> dict[str, str]:
        record = imports.create(
            generated_cv_template_bytes(),
            filename="jobauto-generated-cv.tex",
        )
        draft = drafts.create(
            CandidateDraft.manual(
                import_id=record.import_id,
                mapping=imports.mapping(record.import_id),
                locale=payload.locale,
            )
        )
        return {
            "draft_id": draft.draft_id,
            "page_url": f"/candidate-drafts/{draft.draft_id}",
        }

    @app.get("/setup/imports/{import_id}", response_class=HTMLResponse)
    def tex_import_workspace(request: Request, import_id: str) -> HTMLResponse:
        record = _tex_import_or_404(imports, import_id)
        mapping = imports.mapping(import_id)
        source_lines = imports.source(import_id).decode("utf-8-sig").splitlines()
        return templates.TemplateResponse(
            request=request,
            name="tex_import.html",
            context={
                "record": record,
                "mapping": mapping,
                "source_lines": source_lines,
                "block_kinds": list(TexBlockKind),
                "fidelity_levels": list(FidelityLevel),
            },
        )

    @app.post("/api/tex-imports/{import_id}/mapping")
    def update_tex_mapping(import_id: str, payload: TexMappingUpdate) -> dict[str, object]:
        _tex_import_or_404(imports, import_id)
        try:
            mapping = imports.correct_mapping(import_id, payload.blocks)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "import_id": import_id,
            "source_sha256": mapping.source_sha256,
            "blocks": len(mapping.blocks),
        }

    @app.get("/setup/imports/{import_id}/preview.pdf", response_class=FileResponse)
    def tex_import_preview(import_id: str) -> FileResponse:
        record = _tex_import_or_404(imports, import_id)
        if record.pdf_path is None or not record.pdf_path.is_file():
            raise HTTPException(status_code=409, detail="Original CV has not compiled")
        return FileResponse(
            record.pdf_path,
            media_type="application/pdf",
            filename=f"original_{record.filename.removesuffix('.tex')}.pdf",
            content_disposition_type="inline",
        )

    @app.get("/setup/imports/{import_id}/preview.png", response_class=FileResponse)
    def tex_import_preview_image(import_id: str) -> FileResponse:
        record = _tex_import_or_404(imports, import_id)
        if record.pdf_path is None or not record.pdf_path.is_file():
            raise HTTPException(status_code=409, detail="Original CV has not compiled")
        try:
            preview = render_pdf_first_page(
                record.pdf_path,
                state / "previews" / "imports" / f"{import_id}.png",
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return FileResponse(preview, media_type="image/png")

    def active_profile_extractor() -> CandidateProfileExtractor:
        nonlocal extraction_service
        if extraction_service is None:
            workspace = state / "agent-workspace" / "profile-extraction"
            workspace.mkdir(parents=True, exist_ok=True)
            extraction_service = CandidateProfileExtractor(
                CodexClient.default(cwd=workspace, model=selected_codex_model)
            )
        return extraction_service

    def extract_pdf_profile(pdf_import_id: str) -> None:
        job = drafts.get_job(pdf_import_id).model_copy(update={"status": DraftJobStatus.RUNNING})
        drafts.update_job(job)
        try:
            pdf_record = pdf_imports.get(pdf_import_id)
            extraction = active_profile_extractor().extract_pdf_pages(
                pdf_imports.pages(pdf_import_id)
            )
            generated_record = imports.create(
                generated_cv_template_bytes(),
                filename="jobauto-generated-cv.tex",
            )
            draft = drafts.create(
                CandidateDraft.from_extraction(
                    import_id=generated_record.import_id,
                    mapping=imports.mapping(generated_record.import_id),
                    extraction=extraction,
                    origin=DraftOrigin.PDF,
                    source_document_id=pdf_record.import_id,
                    source_document_filename=pdf_record.filename,
                    source_document_sha256=pdf_record.source_sha256,
                )
            )
            drafts.update_job(
                job.model_copy(
                    update={"status": DraftJobStatus.COMPLETED, "draft_id": draft.draft_id}
                )
            )
        except Exception as exc:
            drafts.update_job(
                job.model_copy(update={"status": DraftJobStatus.FAILED, "error": str(exc)[-2000:]})
            )

    @app.post("/api/pdf-imports", status_code=202)
    async def import_pdf_source(
        request: Request, background_tasks: BackgroundTasks
    ) -> dict[str, str | int]:
        filename = request.headers.get("x-filename", "").strip()
        if not filename:
            raise HTTPException(status_code=422, detail="X-Filename header is required")
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_PDF_SOURCE_BYTES:
                raise HTTPException(status_code=413, detail="PDF CV exceeds the 10 MB limit")
            chunks.append(chunk)
        try:
            record = await run_in_threadpool(
                pdf_imports.create,
                b"".join(chunks),
                filename=filename,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        drafts.start_job(record.import_id)
        background_tasks.add_task(extract_pdf_profile, record.import_id)
        return {
            "import_id": record.import_id,
            "page_count": record.page_count,
            "status_url": f"/api/pdf-imports/{record.import_id}/profile-draft/status",
            "preview_url": f"/api/pdf-imports/{record.import_id}/original.pdf",
        }

    @app.get("/api/pdf-imports/{import_id}/profile-draft/status")
    def pdf_profile_extraction_status(import_id: str) -> dict[str, object]:
        _pdf_import_or_404(pdf_imports, import_id)
        try:
            job = drafts.get_job(import_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Profile extraction not started") from exc
        payload = job.model_dump(mode="json")
        if job.draft_id:
            payload["page_url"] = f"/candidate-drafts/{job.draft_id}"
        return payload

    @app.get("/api/pdf-imports/{import_id}/original.pdf", response_class=FileResponse)
    def pdf_import_preview(import_id: str) -> FileResponse:
        record = _pdf_import_or_404(pdf_imports, import_id)
        return FileResponse(
            record.source_path,
            media_type="application/pdf",
            filename=record.filename,
            content_disposition_type="inline",
        )

    def extract_profile(import_id: str) -> None:
        job = drafts.get_job(import_id).model_copy(update={"status": DraftJobStatus.RUNNING})
        drafts.update_job(job)
        try:
            record = imports.get(import_id)
            mapping = imports.mapping(import_id)
            extraction = active_profile_extractor().extract(imports.source(import_id), mapping)
            draft = drafts.create(
                CandidateDraft.from_extraction(
                    import_id=record.import_id,
                    mapping=mapping,
                    extraction=extraction,
                )
            )
            drafts.update_job(
                job.model_copy(
                    update={"status": DraftJobStatus.COMPLETED, "draft_id": draft.draft_id}
                )
            )
        except Exception as exc:
            drafts.update_job(
                job.model_copy(update={"status": DraftJobStatus.FAILED, "error": str(exc)[-2000:]})
            )

    @app.post("/api/tex-imports/{import_id}/profile-draft", status_code=202)
    def start_profile_extraction(
        import_id: str, background_tasks: BackgroundTasks
    ) -> dict[str, str]:
        _tex_import_or_404(imports, import_id)
        try:
            existing = drafts.get_job(import_id)
        except FileNotFoundError:
            existing = drafts.start_job(import_id)
        if existing.status in {DraftJobStatus.PENDING, DraftJobStatus.FAILED}:
            drafts.update_job(
                existing.model_copy(update={"status": DraftJobStatus.PENDING, "error": None})
            )
            background_tasks.add_task(extract_profile, import_id)
        return {
            "import_id": import_id,
            "status_url": f"/api/tex-imports/{import_id}/profile-draft/status",
        }

    @app.get("/api/tex-imports/{import_id}/profile-draft/status")
    def profile_extraction_status(import_id: str) -> dict[str, object]:
        _tex_import_or_404(imports, import_id)
        try:
            job = drafts.get_job(import_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Profile extraction not started") from exc
        payload = job.model_dump(mode="json")
        if job.draft_id:
            payload["page_url"] = f"/candidate-drafts/{job.draft_id}"
        return payload

    @app.get("/candidate-drafts/{draft_id}", response_class=HTMLResponse)
    def candidate_draft_workspace(request: Request, draft_id: str) -> HTMLResponse:
        try:
            draft = drafts.get(draft_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Candidate draft not found") from exc
        return templates.TemplateResponse(
            request=request,
            name="candidate_draft.html",
            context={"draft": draft, "draft_payload": draft.model_dump(mode="json")},
        )

    @app.get("/api/candidate-drafts/{draft_id}")
    def candidate_draft_payload(draft_id: str) -> dict[str, object]:
        try:
            draft = drafts.get(draft_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Candidate draft not found") from exc
        return draft.model_dump(mode="json")

    @app.put("/api/candidate-drafts/{draft_id}")
    def save_candidate_draft(draft_id: str, payload: CandidateDraftUpdate) -> dict[str, object]:
        try:
            current = drafts.get(draft_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Candidate draft not found") from exc
        try:
            saved = drafts.save(
                update_candidate_draft(current, payload),
                expected_version=payload.expected_version,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return saved.model_dump(mode="json")

    @app.post("/api/candidate-drafts/{draft_id}/validate")
    def validate_profile_draft(draft_id: str) -> dict[str, object]:
        try:
            draft = drafts.get(draft_id)
            mapping = imports.mapping(draft.import_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Candidate draft not found") from exc
        result = validate_candidate_draft(draft, mapping)
        if not result.valid:
            raise HTTPException(status_code=422, detail=result.model_dump(mode="json"))
        if draft.status is not DraftStatus.VALIDATED:
            draft = drafts.save(
                draft.model_copy(update={"status": DraftStatus.VALIDATED}),
                expected_version=draft.version,
            )
        return {
            "valid": True,
            "draft_id": draft.draft_id,
            "version": draft.version,
            "status": draft.status.value,
        }

    @app.post("/api/candidate-drafts/{draft_id}/export", status_code=201)
    def export_profile_draft(draft_id: str) -> dict[str, object]:
        try:
            draft = drafts.get(draft_id)
            source = imports.source(draft.import_id)
            mapping = imports.mapping(draft.import_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Candidate draft not found") from exc
        try:
            profile_path, snapshot = export_candidate_draft(
                draft=draft,
                tex_source=source,
                mapping=mapping,
                profiles_root=candidate_profiles,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        profile_registry.select(snapshot.profile.candidate_id)
        return {
            "candidate_id": snapshot.profile.candidate_id,
            "profile_path": str(profile_path),
            "snapshot_hash": snapshot.snapshot_hash,
            "page_url": f"/profiles/{snapshot.profile.candidate_id}",
            "preview_url": f"/profiles/{snapshot.profile.candidate_id}/preview.pdf",
        }

    @app.get("/profiles/{candidate_id}", response_class=HTMLResponse)
    def profile_workspace(request: Request, candidate_id: str) -> HTMLResponse:
        item = _profile_or_404(catalog, candidate_id)
        profile_registry.select(candidate_id)
        cv_policy = item.policy.documents.get("cv")
        recent_campaigns = active_campaign_service().store.list_for_candidate(candidate_id)
        recent_discoveries = [
            record
            for record in active_discovery_service().store.list_for_candidate(candidate_id)
            if record.status in {"prepared", "ready_for_codex", "running", "blocked"}
        ]
        edit_url = source_draft_url(item)
        return templates.TemplateResponse(
            request=request,
            name="profile.html",
            context={
                "item": item,
                "cv_policy": cv_policy,
                "recent_campaigns": recent_campaigns,
                "recent_discoveries": recent_discoveries,
                "source_draft_url": edit_url,
            },
        )

    @app.get("/profiles/{candidate_id}/preview.pdf", response_class=FileResponse)
    def profile_preview(candidate_id: str) -> FileResponse:
        item = _profile_or_404(catalog, candidate_id)
        preview_dir = state / "previews" / item.profile.profile_hash
        pdf_path = preview_dir / "cv.pdf"
        if not pdf_path.exists():
            preview_dir.mkdir(parents=True, exist_ok=True)
            if item.profile.source_path is None:
                raise HTTPException(status_code=409, detail="Candidate profile is not persisted")
            snapshot = catalog.load_snapshot(item.profile.source_path)
            rendered = DocumentRenderer().render_cv(
                snapshot,
                CvDocumentDraft(
                    document=snapshot.cv_source,
                    provenance=MappingProxyType({}),
                ),
                preview_dir,
            )
            pdf_path = rendered.pdf_path
        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=pdf_path.name,
            content_disposition_type="inline",
        )

    def run_service() -> RunApplicationService:
        nonlocal service
        if service is None:

            def pipeline_factory(snapshot, context, event_callback, run_dir):
                llm = CodexClient.default(
                    cwd=agent_workspace,
                    model=selected_codex_model,
                    event_callback=event_callback,
                )
                pipeline = CandidatePipeline.for_candidate(llm, snapshot, context)
                return CandidateWorkflowPipeline.build(
                    llm=llm,
                    pipeline=pipeline,
                    snapshot=snapshot,
                    run_dir=run_dir,
                )

            service = RunApplicationService(
                repository=catalog.repository,
                store=RunStore(state / "runs"),
                pipeline_factory=pipeline_factory,
            )
        return service

    def active_campaign_service() -> StudioCampaignService:
        nonlocal campaigns
        if campaigns is None:
            campaigns = StudioCampaignService(
                repository=catalog,
                application_service=run_service(),
                store=StudioCampaignStore(state / "campaigns"),
                availability_verifier=HttpOfferAvailabilityVerifier(),
            )
        return campaigns

    def active_discovery_service() -> DiscoveryHandoffService:
        nonlocal discoveries
        if discoveries is None:

            def default_discovery_agent_factory(event_callback):
                return CodexClient.default(
                    cwd=agent_workspace,
                    model=selected_codex_model,
                    event_callback=event_callback,
                )

            discoveries = DiscoveryHandoffService(
                repository=catalog.repository,
                campaign_service=active_campaign_service(),
                store=DiscoveryHandoffStore(state / "discoveries"),
                agent_factory=(discovery_agent_factory or default_discovery_agent_factory),
                availability_verifier=HttpOfferAvailabilityVerifier(),
            )
        return discoveries

    def execute_discovery_flow(discovery_id: str) -> None:
        try:
            _record, campaign = active_discovery_service().execute(discovery_id)
            active_campaign_service().execute(campaign.campaign_id)
        except Exception:
            return
        finally:
            active_discovery_ids.discard(discovery_id)

    def active_handoff_service() -> SubmissionHandoffService:
        nonlocal handoffs
        if handoffs is None:
            handoffs = SubmissionHandoffService(
                repository=catalog,
                campaign_service=active_campaign_service(),
                run_reader=run_service(),
                store=SubmissionHandoffStore(state / "handoffs"),
            )
        return handoffs

    def active_submission_campaign_service() -> SubmissionCampaignService:
        nonlocal submission_campaigns
        if submission_campaigns is None:
            submission_campaigns = SubmissionCampaignService(
                campaign_service=active_campaign_service(),
                handoff_service=active_handoff_service(),
                availability_verifier=HttpOfferAvailabilityVerifier(),
            )
        return submission_campaigns

    def load_cv_change_summary(record) -> CvChangeSummary | None:
        try:
            snapshot = catalog.load_snapshot(record.profile_path)
            return build_cv_change_summary(snapshot.cv_source, record.run_dir)
        except (OSError, TypeError, ValueError):
            return None

    def applications_dashboard(candidate_id: str) -> dict[str, object]:
        profile_item = _profile_or_404(catalog, candidate_id)
        campaigns_for_candidate = active_campaign_service().store.list_for_candidate(
            candidate_id,
            limit=100,
        )
        rows: list[dict[str, object]] = []
        seen_urls: set[str] = set()
        seen_run_ids: set[str] = set()
        for campaign in campaigns_for_candidate:
            handoffs_by_run = {
                record.run_id: record
                for record in active_handoff_service().store.list_for_campaign(campaign.campaign_id)
            }
            for campaign_item in campaign.items:
                if campaign_item.canonical_url in seen_urls:
                    continue
                seen_urls.add(campaign_item.canonical_url)
                run = None
                if campaign_item.run_id:
                    seen_run_ids.add(campaign_item.run_id)
                    try:
                        run = run_service().get(campaign_item.run_id)
                    except FileNotFoundError:
                        pass
                handoff = handoffs_by_run.get(campaign_item.run_id)
                adaptation = _load_adaptation_summary(run.run_dir) if run is not None else None
                baseline_assessment = (adaptation or {}).get("baseline_cv_assessment") or {}
                changes = (
                    build_cv_change_summary(profile_item.source, run.run_dir)
                    if run is not None
                    else None
                )
                review = run.review if run is not None else None
                initial_fit = (
                    campaign_item.offer.semantic_fit_score
                    if campaign_item.offer.semantic_fit_score is not None
                    else campaign_item.evaluation.score
                    if campaign_item.evaluation is not None
                    else None
                )
                status = (
                    handoff.receipt.status
                    if handoff is not None and handoff.receipt is not None
                    else handoff.status
                    if handoff is not None
                    else run.status
                    if run is not None
                    else campaign_item.decision
                )
                skill_names = [
                    skill["name"]
                    for group in (adaptation or {}).get("skill_categories", [])
                    for skill in group["items"]
                ]
                row_id = hashlib.sha256(campaign_item.canonical_url.encode("utf-8")).hexdigest()[
                    :12
                ]
                rows.append(
                    {
                        "row_id": row_id,
                        "campaign_id": campaign.campaign_id,
                        "campaign_updated_at": campaign.updated_at,
                        "company": campaign_item.offer.company,
                        "role": campaign_item.offer.role,
                        "offer_url": campaign_item.offer.url,
                        "description": campaign_item.offer.description,
                        "decision": campaign_item.decision,
                        "status": status,
                        "initial_fit": initial_fit,
                        "initial_fit_rationale": (
                            campaign_item.offer.semantic_fit_rationale
                            or "Fit computed from the candidate's saved search preferences."
                        ),
                        "final_ats": review.get("ats_score") if review else None,
                        "baseline_ats": baseline_assessment.get("ats_score"),
                        "cv_decision": baseline_assessment.get("decision"),
                        "final_quality": review.get("score") if review else None,
                        "run_id": campaign_item.run_id,
                        "run_phase": run.current_phase if run is not None else None,
                        "documents_ready": bool(
                            run is not None
                            and run.artifacts.get("cv")
                            and run.artifacts.get("letter")
                        ),
                        "change_count": changes.change_count if changes is not None else 0,
                        "changed_sections": (
                            changes.changed_sections if changes is not None else []
                        ),
                        "skills": list(dict.fromkeys(skill_names)),
                        "keywords": (adaptation or {}).get("targeted_keywords", []),
                        "blockers": list(
                            dict.fromkeys(
                                [
                                    *campaign_item.reasons,
                                    *(run.blockers if run is not None else []),
                                    *(handoff.blockers if handoff is not None else []),
                                ]
                            )
                        ),
                    }
                )
        service = run_service()
        service_store = getattr(service, "store", None)
        direct_runs = (
            service_store.list_for_candidate(candidate_id, limit=100)
            if service_store is not None and hasattr(service_store, "list_for_candidate")
            else []
        )
        for run in direct_runs:
            if run.run_id in seen_run_ids:
                continue
            try:
                request_payload = RunRequest.model_validate_json(
                    (run.run_dir / "request.json").read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                continue
            adaptation = _load_adaptation_summary(run.run_dir)
            baseline_assessment = (adaptation or {}).get("baseline_cv_assessment") or {}
            changes = load_cv_change_summary(run)
            review = run.review
            skill_names = [
                skill["name"]
                for group in (adaptation or {}).get("skill_categories", [])
                for skill in group["items"]
            ]
            rows.append(
                {
                    "row_id": hashlib.sha256(run.run_id.encode("utf-8")).hexdigest()[:12],
                    "campaign_id": None,
                    "campaign_updated_at": run.updated_at,
                    "company": request_payload.company,
                    "role": request_payload.role,
                    "offer_url": request_payload.offer_url or "",
                    "description": request_payload.offer_text,
                    "decision": "direct",
                    "status": run.status,
                    "initial_fit": None,
                    "initial_fit_rationale": "Direct offer run; no discovery fit score was produced.",
                    "final_ats": review.get("ats_score") if review else None,
                    "baseline_ats": baseline_assessment.get("ats_score"),
                    "cv_decision": baseline_assessment.get("decision"),
                    "final_quality": review.get("score") if review else None,
                    "run_id": run.run_id,
                    "run_phase": run.current_phase,
                    "documents_ready": bool(
                        run.artifacts.get("cv") and run.artifacts.get("letter")
                    ),
                    "change_count": changes.change_count if changes is not None else 0,
                    "changed_sections": changes.changed_sections if changes is not None else [],
                    "skills": list(dict.fromkeys(skill_names)),
                    "keywords": (adaptation or {}).get("targeted_keywords", []),
                    "blockers": list(run.blockers),
                }
            )
        rows.sort(key=lambda row: str(row["campaign_updated_at"]), reverse=True)
        return {
            "candidate_id": candidate_id,
            "items": rows,
            "summary": {
                "offers": len(rows),
                "ready": sum(bool(row["documents_ready"]) for row in rows),
                "submitted": sum(row["status"] == "submitted" for row in rows),
                "active": sum(row["status"] in {"pending", "running"} for row in rows),
                "attention": sum(row["status"] in {"blocked", "failed"} for row in rows),
            },
        }

    @app.get("/profiles/{candidate_id}/applications", response_class=HTMLResponse)
    def applications_workspace(request: Request, candidate_id: str) -> HTMLResponse:
        item = _profile_or_404(catalog, candidate_id)
        dashboard = applications_dashboard(candidate_id)
        return templates.TemplateResponse(
            request=request,
            name="applications.html",
            context={"item": item, "dashboard": dashboard},
        )

    @app.get("/profiles/{candidate_id}/applications/status")
    def applications_status(candidate_id: str) -> dict[str, object]:
        return applications_dashboard(candidate_id)

    @app.post("/profiles/{candidate_id}/runs", status_code=202)
    def start_profile_run(
        candidate_id: str,
        payload: StudioRunRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        item = _profile_or_404(catalog, candidate_id)
        if item.profile.source_path is None:
            raise HTTPException(status_code=409, detail="Candidate profile source is unavailable")
        active_service = run_service()
        run_id = active_service.start(
            RunRequest(
                profile_path=item.profile.source_path,
                offer_text=payload.offer_text,
                offer_url=payload.offer_url,
                company=payload.company,
                role=payload.role,
                max_repairs=payload.max_repairs,
            )
        )
        background_tasks.add_task(active_service.execute, run_id)
        return {
            "run_id": run_id,
            "page_url": f"/runs/{run_id}",
            "status_url": f"/runs/{run_id}/status",
        }

    @app.post("/runs/{run_id}/campaign", status_code=201)
    def attach_direct_run_to_campaign(run_id: str) -> dict[str, str]:
        record = _run_or_404(run_service(), run_id)
        try:
            run_request = RunRequest.model_validate_json(
                (record.run_dir / "request.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="Run request is unavailable") from exc
        if not run_request.offer_url:
            raise HTTPException(
                status_code=409,
                detail="An official offer URL is required before application handoff",
            )
        try:
            campaign = active_campaign_service().attach_completed_run(
                profile_path=run_request.profile_path,
                tracker_path=default_tracker,
                offer=OfferCandidate(
                    company=run_request.company,
                    role=run_request.role,
                    url=run_request.offer_url,
                    posted_at=None,
                    description=run_request.offer_text,
                    source="candidate_manual",
                ),
                run_id=run_id,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "campaign_id": campaign.campaign_id,
            "page_url": f"/campaigns/{campaign.campaign_id}",
        }

    @app.post("/profiles/{candidate_id}/campaigns", status_code=202)
    def start_profile_campaign(
        candidate_id: str,
        payload: StudioCampaignRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        item = _profile_or_404(catalog, candidate_id)
        if item.profile.source_path is None:
            raise HTTPException(status_code=409, detail="Candidate profile source is unavailable")
        active_service = active_campaign_service()
        try:
            record = active_service.create(
                profile_path=item.profile.source_path,
                tracker_path=payload.tracker_path or default_tracker,
                candidates=payload.candidates,
                limit=payload.limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background_tasks.add_task(active_service.execute, record.campaign_id)
        return {
            "campaign_id": record.campaign_id,
            "selected_count": record.selected_count,
            "page_url": f"/campaigns/{record.campaign_id}",
            "status_url": f"/campaigns/{record.campaign_id}/status",
        }

    @app.post("/profiles/{candidate_id}/discoveries", status_code=201)
    def prepare_profile_discovery(
        candidate_id: str,
        payload: StudioDiscoveryRequest,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        item = _profile_or_404(catalog, candidate_id)
        if item.profile.source_path is None:
            raise HTTPException(status_code=409, detail="Candidate profile source is unavailable")
        try:
            record = active_discovery_service().prepare(
                profile_path=item.profile.source_path,
                tracker_path=payload.tracker_path or default_tracker,
                requested_count=payload.requested_count,
                conversation_url=payload.conversation_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        active_discovery_ids.add(record.discovery_id)
        background_tasks.add_task(execute_discovery_flow, record.discovery_id)
        return {
            "discovery_id": record.discovery_id,
            "page_url": f"/discoveries/{record.discovery_id}",
            "status_url": f"/discoveries/{record.discovery_id}/status",
        }

    @app.get("/discoveries/{discovery_id}", response_class=HTMLResponse)
    def discovery_workspace(request: Request, discovery_id: str) -> HTMLResponse:
        record = _discovery_or_404(active_discovery_service(), discovery_id)
        snapshot = catalog.load_snapshot(record.profile_path)
        return templates.TemplateResponse(
            request=request,
            name="discovery.html",
            context={
                "record": record,
                "prompt": record.prompt_path.read_text(encoding="utf-8"),
                "search_preferences": snapshot.search_preferences,
            },
        )

    @app.get("/discoveries/{discovery_id}/status")
    def discovery_status(discovery_id: str) -> dict[str, object]:
        record = _discovery_or_404(active_discovery_service(), discovery_id)
        payload = record.model_dump(mode="json")
        payload["interrupted"] = (
            record.status == "running" and discovery_id not in active_discovery_ids
        )
        payload["last_activity_at"] = record.updated_at
        return payload

    @app.post("/discoveries/{discovery_id}/resume", status_code=202)
    def resume_discovery(
        discovery_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict[str, object]:
        record = _discovery_or_404(active_discovery_service(), discovery_id)
        if discovery_id in active_discovery_ids:
            return {
                "discovery_id": discovery_id,
                "status": record.status,
                "already_running": True,
            }
        try:
            record = active_discovery_service().retry_interrupted(discovery_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        active_discovery_ids.add(discovery_id)
        background_tasks.add_task(execute_discovery_flow, discovery_id)
        return {
            "discovery_id": discovery_id,
            "status": record.status,
            "already_running": False,
        }

    @app.post("/discoveries/{discovery_id}/cancel", status_code=202)
    def cancel_discovery(discovery_id: str) -> dict[str, object]:
        try:
            record = active_discovery_service().request_cancel(discovery_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @app.post("/discoveries/{discovery_id}/candidates", status_code=202)
    def import_discovery_candidates(
        discovery_id: str,
        payload: StudioDiscoveryImport,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        try:
            record, campaign = active_discovery_service().import_candidates(
                discovery_id,
                candidates=payload.candidates,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        background_tasks.add_task(active_campaign_service().execute, campaign.campaign_id)
        return {
            "discovery_id": record.discovery_id,
            "campaign_id": campaign.campaign_id,
            "page_url": f"/campaigns/{campaign.campaign_id}",
            "status_url": f"/campaigns/{campaign.campaign_id}/status",
        }

    @app.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
    def campaign_workspace(request: Request, campaign_id: str) -> HTMLResponse:
        record = _campaign_or_404(active_campaign_service(), campaign_id)
        submission = active_submission_campaign_service().get(campaign_id)
        return templates.TemplateResponse(
            request=request,
            name="campaign.html",
            context={"record": record, "submission": submission},
        )

    @app.get("/campaigns/{campaign_id}/status")
    def campaign_status(campaign_id: str) -> dict[str, object]:
        return _campaign_payload(_campaign_or_404(active_campaign_service(), campaign_id))

    @app.post("/campaigns/{campaign_id}/resume", status_code=202)
    def resume_campaign(
        campaign_id: str,
        background_tasks: BackgroundTasks,
    ) -> dict[str, str]:
        try:
            record = active_campaign_service().resume(campaign_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if record.status != "completed":
            background_tasks.add_task(active_campaign_service().execute, record.campaign_id)
        return {
            "campaign_id": record.campaign_id,
            "page_url": f"/campaigns/{record.campaign_id}",
            "status_url": f"/campaigns/{record.campaign_id}/status",
        }

    @app.post("/campaigns/{campaign_id}/cancel", status_code=202)
    def cancel_campaign(campaign_id: str) -> dict[str, object]:
        try:
            record = active_campaign_service().request_cancel(campaign_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _campaign_payload(record)

    @app.post("/campaigns/{campaign_id}/expand", status_code=202)
    def expand_campaign(
        campaign_id: str,
        background_tasks: BackgroundTasks,
        payload: StudioCampaignExpansionRequest | None = None,
    ) -> dict[str, object]:
        try:
            record = active_campaign_service().expand(
                campaign_id,
                additional_count=payload.additional_count if payload else None,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        background_tasks.add_task(active_campaign_service().execute, record.campaign_id)
        return {
            **_campaign_payload(record),
            "page_url": f"/campaigns/{record.campaign_id}",
            "status_url": f"/campaigns/{record.campaign_id}/status",
        }

    @app.post("/campaigns/{campaign_id}/submission", status_code=201)
    def prepare_submission_campaign(
        campaign_id: str,
        payload: StudioSubmissionRequest | None = None,
    ) -> dict[str, object]:
        mode = payload.mode if payload else None
        try:
            summary = active_submission_campaign_service().prepare(campaign_id, mode=mode)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response: dict[str, object] = {
            **summary.model_dump(mode="json"),
            "page_url": f"/campaigns/{campaign_id}",
            "status_url": f"/campaigns/{campaign_id}/submission/status",
        }
        if summary.mode in {SubmissionMode.CONFIRM, SubmissionMode.AUTOMATIC}:
            prompt = _codex_submission_prompt(campaign_id, summary.mode)
            response["codex_prompt"] = prompt
            response["codex_url"] = f"codex://new?{urlencode({'prompt': prompt})}"
        return response

    @app.get("/campaigns/{campaign_id}/submission/status")
    def submission_campaign_status(campaign_id: str) -> dict[str, object]:
        try:
            return active_submission_campaign_service().get(campaign_id).model_dump(mode="json")
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Studio campaign not found") from exc

    @app.post("/campaigns/{campaign_id}/submission/claim-next")
    def claim_next_submission(campaign_id: str) -> dict[str, object]:
        try:
            summary, record = active_submission_campaign_service().claim_next(campaign_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "queue": summary.model_dump(mode="json"),
            "packet": record.model_dump(mode="json") if record is not None else None,
        }

    @app.post("/campaigns/{campaign_id}/handoffs/{run_id}", status_code=201)
    def prepare_submission_handoff(campaign_id: str, run_id: str) -> dict[str, str]:
        try:
            record = active_handoff_service().prepare(campaign_id, run_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "handoff_id": record.handoff_id,
            "status": record.status,
            "page_url": f"/handoffs/{record.handoff_id}",
            "record_url": f"/handoffs/{record.handoff_id}/status",
        }

    @app.get("/handoffs/{handoff_id}", response_class=HTMLResponse)
    def submission_handoff_workspace(request: Request, handoff_id: str) -> HTMLResponse:
        record = _handoff_or_404(active_handoff_service(), handoff_id)
        return templates.TemplateResponse(
            request=request,
            name="handoff.html",
            context={"record": record},
        )

    @app.get("/handoffs/{handoff_id}/status")
    def submission_handoff_status(handoff_id: str) -> dict[str, object]:
        return _handoff_or_404(active_handoff_service(), handoff_id).model_dump(mode="json")

    @app.get("/sandbox/apply/{handoff_id}", response_class=HTMLResponse)
    def sandbox_application(request: Request, handoff_id: str) -> HTMLResponse:
        record = _handoff_or_404(active_handoff_service(), handoff_id)
        if record.status not in {"ready_for_chrome", "claimed_for_chrome"}:
            raise HTTPException(status_code=409, detail="handoff is not ready for Chrome")
        return templates.TemplateResponse(
            request=request,
            name="sandbox_apply.html",
            context={"record": record},
        )

    @app.post("/sandbox/apply/{handoff_id}/submit", status_code=201)
    def submit_sandbox_application(
        request: Request,
        handoff_id: str,
        payload: SandboxSubmission,
    ) -> dict[str, str]:
        record = _handoff_or_404(active_handoff_service(), handoff_id)
        if record.status not in {"ready_for_chrome", "claimed_for_chrome"}:
            raise HTTPException(status_code=409, detail="handoff is not ready for Chrome")
        if record.candidate_identity is not None:
            expected_name = (
                f"{record.candidate_identity.first_name} {record.candidate_identity.last_name}"
            )
            if payload.full_name.strip().casefold() != expected_name.casefold():
                raise HTTPException(status_code=422, detail="candidate name does not match handoff")
            if payload.email.strip().casefold() != record.candidate_identity.email.casefold():
                raise HTTPException(
                    status_code=422, detail="candidate email does not match handoff"
                )
        expected = {artifact.kind: artifact for artifact in record.artifacts}
        for kind, uploaded in (("cv", payload.cv), ("letter", payload.letter)):
            try:
                raw = base64.b64decode(uploaded.content_base64, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise HTTPException(status_code=422, detail=f"{kind} is not valid base64") from exc
            if hashlib.sha256(raw).hexdigest() != expected[kind].sha256:
                raise HTTPException(
                    status_code=422, detail=f"{kind} does not match approved artifact"
                )
        confirmation_url = str(request.url_for("sandbox_confirmation", handoff_id=handoff_id))
        updated = active_handoff_service().record_receipt(
            handoff_id,
            SubmissionReceipt(
                status="sandbox_verified",
                portal="jobauto_sandbox",
                confirmation_url=confirmation_url,
                filled_fields=["full_name", "email", "location", "message"],
                uploaded_files=[payload.cv.name, payload.letter.name],
            ),
            allow_sandbox=True,
        )
        return {
            "handoff_id": updated.handoff_id,
            "status": updated.status,
            "confirmation_url": confirmation_url,
        }

    @app.get(
        "/sandbox/confirmation/{handoff_id}",
        response_class=HTMLResponse,
        name="sandbox_confirmation",
    )
    def sandbox_confirmation(request: Request, handoff_id: str) -> HTMLResponse:
        record = _handoff_or_404(active_handoff_service(), handoff_id)
        if record.status != "sandbox_verified" or record.receipt is None:
            raise HTTPException(status_code=409, detail="sandbox verification is not confirmed")
        return templates.TemplateResponse(
            request=request,
            name="sandbox_confirmation.html",
            context={"record": record},
        )

    @app.post("/handoffs/{handoff_id}/receipt")
    def record_submission_receipt(
        handoff_id: str,
        receipt: SubmissionReceipt,
    ) -> dict[str, object]:
        try:
            record = active_handoff_service().record_receipt(handoff_id, receipt)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @app.post("/handoffs/{handoff_id}/release")
    def release_submission_claim(handoff_id: str) -> dict[str, object]:
        try:
            record = active_handoff_service().release_claim(handoff_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return record.model_dump(mode="json")

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_workspace(request: Request, run_id: str) -> HTMLResponse:
        record = _run_or_404(run_service(), run_id)
        adaptation_summary = _load_adaptation_summary(record.run_dir)
        change_summary = load_cv_change_summary(record)
        application_request = None
        try:
            application_request = RunRequest.model_validate_json(
                (record.run_dir / "request.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            pass
        campaign_id = None
        initial_fit = None
        for campaign in active_campaign_service().store.list_for_candidate(
            record.candidate_id,
            limit=100,
        ):
            campaign_item = next(
                (item for item in campaign.items if item.run_id == record.run_id),
                None,
            )
            if campaign_item is None:
                continue
            campaign_id = campaign.campaign_id
            initial_fit = (
                campaign_item.offer.semantic_fit_score
                if campaign_item.offer.semantic_fit_score is not None
                else campaign_item.evaluation.score
                if campaign_item.evaluation is not None
                else None
            )
            break
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "record": record,
                "application_request": application_request,
                "adaptation_summary": adaptation_summary,
                "change_summary": change_summary,
                "initial_fit": initial_fit,
                "baseline_assessment": ((adaptation_summary or {}).get("baseline_cv_assessment")),
                "back_url": (
                    f"/campaigns/{campaign_id}"
                    if campaign_id
                    else f"/profiles/{record.candidate_id}"
                ),
                "back_label": "Application batch" if campaign_id else "Candidate profile",
                "campaign_id": campaign_id,
            },
        )

    @app.get("/runs/{run_id}/status")
    def run_status(run_id: str) -> dict[str, object]:
        record = _run_or_404(run_service(), run_id)
        payload = record.model_dump(mode="json")
        payload["adaptation_summary"] = _load_adaptation_summary(record.run_dir)
        change_summary = load_cv_change_summary(record)
        payload["change_summary"] = (
            change_summary.model_dump(mode="json") if change_summary is not None else None
        )
        return payload

    def resolve_run_artifact(record, kind: str) -> Path:
        if kind == "original-cv":
            path = record.run_dir / "source-artifacts" / "cv.pdf"
            if not path.is_file():
                source_path = path.with_suffix(".tex")
                if not source_path.is_file():
                    try:
                        snapshot = catalog.load_snapshot(record.profile_path)
                    except (OSError, ValueError) as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="The original CV snapshot is unavailable for this older run",
                        ) from exc
                    if snapshot.snapshot_hash != record.snapshot_hash:
                        raise HTTPException(
                            status_code=409,
                            detail="The candidate profile changed after this older run",
                        )
                    source_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path.write_bytes(snapshot.cv_template_bytes)
                try:
                    path, _log_path = compile_latex(source_path, path.parent / "build")
                except RuntimeError as exc:
                    raise HTTPException(status_code=409, detail=str(exc)) from exc
        elif kind in {"cv", "letter"} and kind in record.artifacts:
            path = Path(str(record.artifacts[kind]["pdf_path"])).resolve()
        else:
            raise HTTPException(status_code=404, detail="Run artifact not found")
        try:
            path.relative_to(record.run_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Run artifact path is invalid") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Run artifact not found")
        return path

    @app.get("/runs/{run_id}/artifacts/{kind}", response_class=FileResponse)
    def run_artifact(run_id: str, kind: str) -> FileResponse:
        record = _run_or_404(run_service(), run_id)
        path = resolve_run_artifact(record, kind)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
            content_disposition_type="inline",
        )

    @app.get("/runs/{run_id}/previews/{kind}.png", response_class=FileResponse)
    def run_artifact_preview(run_id: str, kind: str) -> FileResponse:
        record = _run_or_404(run_service(), run_id)
        path = resolve_run_artifact(record, kind)
        short_source = path.parent / f"{kind}.pdf"
        preview_source = short_source if short_source.is_file() else path
        try:
            preview = render_pdf_first_page(
                preview_source,
                record.run_dir / "previews" / f"{kind}.png",
            )
        except (OSError, RuntimeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return FileResponse(preview, media_type="image/png")

    return app


def _load_adaptation_summary(run_dir: Path) -> dict[str, object] | None:
    """Return the brief decisions useful to a candidate, without exposing the full agent packet."""
    try:
        brief = ApplicationBrief.model_validate_json(
            (run_dir / "application-brief.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError, TypeError):
        return None

    return {
        "normalized_role": brief.normalized_role,
        "sector": brief.sector,
        "specialisations": brief.specialisations,
        "cv_angle": brief.cv_angle,
        "letter_angle": brief.letter_angle,
        "decisions": [
            {
                "surface": item.surface,
                "decision": item.decision,
                "rationale": item.rationale,
                "evidence_count": len(set(item.fact_ids)),
            }
            for item in brief.adaptation_decisions
        ],
        "project_plan": {
            "decision": brief.project_plan.decision,
            "rationale": brief.project_plan.rationale,
            "slots": [
                {
                    "slot": item.slot,
                    "mode": item.mode,
                    "rationale": item.rationale,
                }
                for item in brief.project_plan.slots
            ],
        },
        "skill_categories": [
            {
                "category": category,
                "items": [
                    {
                        "name": item.name,
                        "evidence_level": item.evidence_level,
                        "priority": item.priority,
                    }
                    for item in brief.skill_plan.items
                    if item.category == category
                ],
            }
            for category in brief.skill_plan.categories
        ],
        "targeted_keywords": brief.targeted_keywords,
        "baseline_cv_assessment": (
            brief.baseline_cv_assessment.model_dump(mode="json")
            if brief.baseline_cv_assessment is not None
            else None
        ),
    }


def _ensure_default_tracker(path: Path) -> None:
    with tracker_lock(path):
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.title = "Postulations"
            ensure_tracker_schema(sheet)
            save_workbook_atomically(workbook, path)
        finally:
            workbook.close()


def _profile_or_404(catalog: ProfileCatalog, candidate_id: str) -> StudioProfile:
    try:
        return catalog.get(candidate_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Candidate profile not found") from exc


def _run_or_404(service: RunApplicationService, run_id: str):
    try:
        return service.get(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Application run not found") from exc


def _latest_profile_versions(items: list) -> list:
    """Show one workspace per draft while retaining every exported version on disk."""
    latest: dict[str, tuple[tuple[int, float], object]] = {}
    order: list[str] = []
    for item in items:
        source_dir = item.profile.source_path.parent
        lineage = item.profile.candidate_id
        try:
            metadata = json.loads((source_dir / "studio_source.json").read_text(encoding="utf-8"))
            lineage = str(metadata.get("draft_id") or lineage)
        except (OSError, ValueError, TypeError):
            pass
        version_match = re.search(r"-v(\d+)$", item.profile.candidate_id)
        version = int(version_match.group(1)) if version_match else 0
        try:
            modified_at = item.profile.source_path.stat().st_mtime
        except OSError:
            modified_at = 0.0
        if lineage not in latest:
            order.append(lineage)
        if lineage not in latest or (version, modified_at) > latest[lineage][0]:
            latest[lineage] = ((version, modified_at), item)
    return [latest[lineage][1] for lineage in order]


def _campaign_or_404(
    service: StudioCampaignService,
    campaign_id: str,
) -> StudioCampaignRecord:
    try:
        return service.get(campaign_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Studio campaign not found") from exc


def _discovery_or_404(
    service: DiscoveryHandoffService,
    discovery_id: str,
) -> DiscoveryHandoffRecord:
    try:
        return service.get(discovery_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Studio discovery not found") from exc


def _campaign_payload(record: StudioCampaignRecord) -> dict[str, object]:
    payload = record.model_dump(mode="json")
    payload["selected_count"] = record.selected_count
    payload["reserve_count"] = record.reserve_count
    payload["completed_count"] = sum(
        item.decision == "selected" and item.run_status == "completed" for item in record.items
    )
    payload["blocked_count"] = sum(
        item.decision == "selected" and item.run_status == "blocked" for item in record.items
    )
    payload["failed_count"] = sum(
        item.decision == "selected" and item.run_status == "failed" for item in record.items
    )
    return payload


def _codex_submission_prompt(campaign_id: str, mode: SubmissionMode) -> str:
    action = (
        "Fill every application, upload the approved CV and cover letter, perform the "
        "final review, submit when the employer action is unambiguous, capture the "
        "confirmation, and record the receipt. Continue until the queue is empty."
        if mode is SubmissionMode.AUTOMATIC
        else "Fill every application and upload the approved CV and cover letter. Stop "
        "immediately before each employer's final submit action so I can confirm it."
    )
    return (
        "[@JobAuto](plugin://jobauto@jobauto-studio) Process JobAuto campaign "
        f"{campaign_id} from its local Studio queue using my authenticated Chrome "
        f"extension. The queue mode is {mode.value}. {action}"
    )


def _handoff_or_404(
    service: SubmissionHandoffService,
    handoff_id: str,
) -> SubmissionHandoffRecord:
    try:
        return service.get(handoff_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Submission handoff not found") from exc


def _tex_import_or_404(store: TexImportStore, import_id: str):
    try:
        return store.get(import_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Imported CV not found") from exc


def _pdf_import_or_404(store: PdfImportStore, import_id: str):
    try:
        return store.get(import_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Imported PDF CV not found") from exc
