from __future__ import annotations

import shutil
from pathlib import Path

from jobauto.application_service import RunApplicationService, RunRequest
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.document_patch import (
    CandidateDocumentDraft,
    CvAdaptationPatch,
    CvFieldChange,
    apply_cv_patch,
)
from jobauto.models import (
    ApplicationBrief,
    CandidateApplicationReview,
    CandidateLetterDraft,
    CandidateRepairAction,
    LetterArgumentAssessment,
    LetterArgumentCriterionAssessment,
)
from jobauto.run_store import RunStore


def _profiles_root() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "profiles"


def _passing_letter_argument() -> LetterArgumentAssessment:
    def criterion() -> LetterArgumentCriterionAssessment:
        return LetterArgumentCriterionAssessment(
            state="pass",
            rationale="The rendered letter provides concrete support for this criterion.",
            supporting_excerpt="I am applying for the Data Engineer role",
        )

    return LetterArgumentAssessment(
        target_specificity=criterion(),
        evidence_to_missions=criterion(),
        candidate_contribution=criterion(),
        motivation_credibility=criterion(),
        tone_and_naturalness=criterion(),
    )


class StubCandidatePipeline:
    def __init__(
        self,
        snapshot,
        *,
        approve: bool = True,
        approve_after_repair: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.approve = approve
        self.approve_after_repair = approve_after_repair
        self.repairs = 0

    def generate_candidate_documents(self, _row, _offer_text, *, project_lab_context=""):
        patch = CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="summary.text",
                    value=(
                        "Data engineer focused on reliable energy-data pipelines, data quality "
                        "and analytics products, with hands-on experience translating operational "
                        "requirements into maintainable Python and SQL data flows for business users."
                    ),
                    fact_ids=["identity.current"],
                )
            ]
        )
        return CandidateDocumentDraft(
            brief=ApplicationBrief.model_construct(
                company="GridCo",
                role="Data Engineer",
                language="en",
            ),
            cv_patch=patch,
            cv=apply_cv_patch(self.snapshot, patch),
            letter=CandidateLetterDraft(
                greeting="Dear hiring team,",
                paragraphs=[
                    "I am applying for the Data Engineer role because its focus on reliable operational data products matches my analytics-pipeline experience."
                ],
                closing="Kind regards,\nAlex Morgan",
                used_fact_ids=["identity.current"],
            ),
        )

    def review_candidate_documents(
        self,
        _row,
        _package,
        cv_rendered,
        letter_rendered,
        _offer_text,
    ):
        assert len(cv_rendered.pdf_sha256) == 64
        assert len(letter_rendered.pdf_sha256) == 64
        approved = self.approve or (self.approve_after_repair and self.repairs > 0)
        return CandidateApplicationReview(
            approved=approved,
            score=94 if approved else 72,
            ats_score=92,
            editorial_score=94,
            adaptation_score=93,
            blocking_issues=[] if approved else ["Central requirement is not visible."],
            warnings=[],
            letter_argument=_passing_letter_argument(),
            requirement_coverage=[],
            repair_actions=(
                []
                if approved
                else [
                    CandidateRepairAction(
                        surface="cv",
                        instruction="Make the central requirement visible using verified evidence.",
                    )
                ]
            ),
        )

    def repair_candidate_documents(
        self,
        _row,
        package,
        _review,
        _offer_text,
    ):
        self.repairs += 1
        return package


def test_application_service_runs_one_offer_to_hashed_pdfs(tmp_path: Path) -> None:
    repository = CandidateProfileRepository(_profiles_root())
    service = RunApplicationService(
        repository=repository,
        store=RunStore(tmp_path / "runs"),
        pipeline_factory=lambda snapshot, _context: StubCandidatePipeline(snapshot),
    )
    request = RunRequest(
        profile_path=_profiles_root() / "example" / "profile.yaml",
        offer_text="GridCo seeks a Data Engineer to build Python and SQL pipelines for operational energy data.",
        offer_url="https://example.test/jobs/data-engineer",
        company="GridCo",
        role="Data Engineer",
    )

    run_id = service.start(request)
    pending = service.get(run_id)
    completed = service.execute(run_id)

    assert pending.status == "pending"
    assert completed.status == "completed"
    assert completed.phase_history == [
        "pending",
        "loading_context",
        "generating_documents",
        "rendering_documents",
        "reviewing_documents",
        "completed",
    ]
    assert completed.artifacts["cv"]["page_count"] == 1
    assert completed.artifacts["cv"]["extracted_text_characters"] > 500
    assert completed.artifacts["cv"]["layout_metrics"]["vertical_coverage_ratio"] > 0
    assert completed.artifacts["letter"]["page_count"] == 1
    cv_path = Path(str(completed.artifacts["cv"]["pdf_path"]))
    letter_path = Path(str(completed.artifacts["letter"]["pdf_path"]))
    assert cv_path.is_file()
    assert cv_path.name == "CV_Alex_Morgan_Data_Engineer_GridCo.pdf"
    assert letter_path.name == "Lettre_Alex_Morgan_Data_Engineer_GridCo.pdf"
    assert (cv_path.parent / "cv.pdf").is_file()
    source_tex = completed.run_dir / "source-artifacts" / "cv.tex"
    assert (
        source_tex.read_bytes() == repository.load_snapshot(request.profile_path).cv_template_bytes
    )
    assert (completed.run_dir / "context" / "candidate_context.json").is_file()
    assert (completed.run_dir / "candidate-package-1.json").is_file()
    assert (completed.run_dir / "review-1.json").is_file()
    assert completed.review["approved"] is True

    legacy_artifacts = {kind: dict(payload) for kind, payload in completed.artifacts.items()}
    legacy_artifacts["cv"]["pdf_path"] = str(cv_path.parent / "cv.pdf")
    service.store.save(completed.model_copy(update={"artifacts": legacy_artifacts}))
    republished = service.get(run_id)
    assert Path(str(republished.artifacts["cv"]["pdf_path"])).name == cv_path.name


def test_application_service_persists_profile_drift_as_blocker(tmp_path: Path) -> None:
    profiles_root = tmp_path / "profiles"
    source = _profiles_root() / "example"
    shutil.copytree(source, profiles_root / "example")
    repository = CandidateProfileRepository(profiles_root)
    service = RunApplicationService(
        repository=repository,
        store=RunStore(tmp_path / "runs"),
        pipeline_factory=lambda snapshot, _context: StubCandidatePipeline(snapshot),
    )
    profile_path = profiles_root / "example" / "profile.yaml"
    run_id = service.start(
        RunRequest(
            profile_path=profile_path,
            offer_text="A sufficiently long synthetic offer for a data engineering position.",
        )
    )
    facts = profiles_root / "example" / "facts.yaml"
    facts.write_text(facts.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    blocked = service.execute(run_id)

    assert blocked.status == "blocked"
    assert blocked.blockers == ["candidate profile changed after the run was created"]


def test_application_service_never_completes_a_rejected_package(tmp_path: Path) -> None:
    service = RunApplicationService(
        repository=CandidateProfileRepository(_profiles_root()),
        store=RunStore(tmp_path / "runs"),
        pipeline_factory=lambda snapshot, _context: StubCandidatePipeline(
            snapshot,
            approve=False,
        ),
    )
    run_id = service.start(
        RunRequest(
            profile_path=_profiles_root() / "example" / "profile.yaml",
            offer_text="GridCo seeks a Data Engineer to build reliable Python and SQL pipelines.",
        )
    )

    blocked = service.execute(run_id)

    assert blocked.status == "blocked"
    assert blocked.current_phase == "review_blocked"
    assert blocked.review["approved"] is False
    assert blocked.blockers == ["Central requirement is not visible."]


def test_application_service_rerenders_and_reviews_one_successful_repair(
    tmp_path: Path,
) -> None:
    holder = {}

    def factory(snapshot, _context):
        pipeline = StubCandidatePipeline(snapshot, approve=False, approve_after_repair=True)
        holder["pipeline"] = pipeline
        return pipeline

    service = RunApplicationService(
        repository=CandidateProfileRepository(_profiles_root()),
        store=RunStore(tmp_path / "runs"),
        pipeline_factory=factory,
    )
    run_id = service.start(
        RunRequest(
            profile_path=_profiles_root() / "example" / "profile.yaml",
            offer_text="GridCo seeks a Data Engineer to build reliable Python and SQL pipelines.",
        )
    )

    completed = service.execute(run_id)

    assert completed.status == "completed"
    assert holder["pipeline"].repairs == 1
    assert completed.phase_history[-5:] == [
        "reviewing_documents",
        "repairing_documents",
        "rendering_documents",
        "reviewing_documents",
        "completed",
    ]
    assert (completed.run_dir / "review-1.json").is_file()
    assert (completed.run_dir / "review-2.json").is_file()


def test_application_service_persists_live_agent_events(tmp_path: Path) -> None:
    def factory(snapshot, _context, event_callback):
        pipeline = StubCandidatePipeline(snapshot)
        original = pipeline.generate_candidate_documents

        def generate(*args, **kwargs):
            event_callback(
                {
                    "call_id": "offer-analysis-1",
                    "phase": "offer_analysis",
                    "status": "running",
                    "attempt": 1,
                }
            )
            package = original(*args, **kwargs)
            event_callback(
                {
                    "call_id": "offer-analysis-1",
                    "phase": "offer_analysis",
                    "status": "succeeded",
                    "attempt": 1,
                    "latency_ms": 12,
                }
            )
            event_callback(
                {
                    "call_id": "offer-analysis-1",
                    "phase": "offer_analysis",
                    "status": "succeeded",
                    "attempt": 1,
                    "latency_ms": 12,
                    "pipeline_outcome": "accepted",
                }
            )
            return package

        pipeline.generate_candidate_documents = generate
        return pipeline

    service = RunApplicationService(
        repository=CandidateProfileRepository(_profiles_root()),
        store=RunStore(tmp_path / "runs"),
        pipeline_factory=factory,
    )
    run_id = service.start(
        RunRequest(
            profile_path=_profiles_root() / "example" / "profile.yaml",
            offer_text="GridCo seeks a Data Engineer to build reliable Python and SQL pipelines.",
        )
    )

    completed = service.execute(run_id)

    assert completed.status == "completed"
    assert [event["status"] for event in completed.agent_events] == [
        "running",
        "succeeded",
    ]
    assert completed.agent_events[-1]["pipeline_outcome"] == "accepted"
    assert completed.agent_events[-1]["call_id"] == "offer-analysis-1"
    assert "agent:offer_analysis:running" in completed.phase_history
    assert (completed.run_dir / "agent-events.jsonl").read_text(encoding="utf-8").count(
        "offer_analysis"
    ) == 3
