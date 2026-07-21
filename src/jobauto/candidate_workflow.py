from __future__ import annotations

from pathlib import Path

from jobauto.candidate_pipeline import CandidatePipeline
from jobauto.candidate_snapshot import CandidateSnapshot
from jobauto.models import ApplicationRow
from jobauto.project_lab import (
    GithubInspirationProvider,
    ProjectLabFamily,
    ProjectLabService,
    format_project_lab_prompt_context,
    write_project_lab_outputs,
)


class CandidateWorkflowPipeline:
    """Compose candidate Project Lab planning with the document pipeline."""

    def __init__(
        self,
        *,
        pipeline: CandidatePipeline,
        project_lab: ProjectLabService,
        snapshot: CandidateSnapshot,
        run_dir: Path,
    ) -> None:
        self._pipeline = pipeline
        self._project_lab = project_lab
        self._snapshot = snapshot
        self._run_dir = run_dir
        self._project_lab_context = ""

    @classmethod
    def build(
        cls,
        *,
        llm,
        pipeline: CandidatePipeline,
        snapshot: CandidateSnapshot,
        run_dir: Path,
    ) -> CandidateWorkflowPipeline:
        policy = snapshot.profile.project_lab
        provider = GithubInspirationProvider() if policy.allow_external_inspiration else None
        project_lab = ProjectLabService(
            llm=llm,
            facts=snapshot.facts,
            skill_policy=snapshot.skill_policy,
            project_bank=snapshot.project_bank,
            cv_reference=snapshot.cv_template,
            external_inspiration_provider=provider,
        )
        return cls(
            pipeline=pipeline,
            project_lab=project_lab,
            snapshot=snapshot,
            run_dir=run_dir,
        )

    def generate_candidate_documents(
        self,
        row: ApplicationRow,
        offer_text: str,
        *,
        project_lab_context: str = "",
    ):
        brief = self._pipeline.generate_lean_brief(
            row,
            offer_text,
            project_lab_context=project_lab_context,
        )
        if (
            not project_lab_context
            and (
                brief.baseline_cv_assessment is None
                or brief.baseline_cv_assessment.decision != "keep_baseline"
            )
            and _project_lab_is_needed(brief)
        ):
            project_lab_context = self._prepare_project_lab(row, offer_text, brief)
        self._project_lab_context = project_lab_context
        (self._run_dir / "application-brief.json").write_text(
            brief.model_dump_json(indent=2),
            encoding="utf-8",
            newline="\n",
        )
        offer_contract = getattr(self._pipeline, "last_offer_contract", None)
        if offer_contract is not None:
            (self._run_dir / "offer-contract.json").write_text(
                offer_contract.model_dump_json(indent=2),
                encoding="utf-8",
                newline="\n",
            )
        baseline_coverage = getattr(self._pipeline, "last_baseline_coverage", None)
        if baseline_coverage is not None:
            (self._run_dir / "baseline-cv-coverage.json").write_text(
                baseline_coverage.model_dump_json(indent=2),
                encoding="utf-8",
                newline="\n",
            )
        candidate_evidence = getattr(self._pipeline, "last_candidate_evidence", None)
        if candidate_evidence is not None:
            (self._run_dir / "candidate-evidence-assessment.json").write_text(
                candidate_evidence.model_dump_json(indent=2),
                encoding="utf-8",
                newline="\n",
            )
        return self._pipeline.generate_candidate_documents(
            row,
            offer_text,
            brief=brief,
            project_lab_context=project_lab_context,
        )

    def repair_candidate_documents(self, row, package, review, offer_text):
        return self._pipeline.repair_candidate_documents(
            row,
            package,
            review,
            offer_text,
            project_lab_context=self._project_lab_context,
        )

    def _prepare_project_lab(self, row: ApplicationRow, offer_text: str, brief) -> str:
        policy = self._snapshot.profile.project_lab
        if policy.maximum_visible_projects == 0:
            return ""
        families = [ProjectLabFamily.REAL_PROJECT]
        if policy.allow_new_project:
            families.extend(
                [
                    ProjectLabFamily.PERSONAL_PROJECT_INSPIRED,
                    ProjectLabFamily.SYNTHETIC_PROJECT,
                ]
            )
        project_plan = getattr(brief, "project_plan", None)
        planned_slots = getattr(project_plan, "slots", [])
        slot_budget = len(planned_slots)
        if not policy.minimum_visible_projects <= slot_budget <= policy.maximum_visible_projects:
            raise ValueError(
                "validated project plan slot count violates the candidate Project Lab policy"
            )
        result = self._project_lab.suggest(
            row,
            offer_text,
            families=families,
            profile=brief,
            cv_slot_budget=slot_budget,
        )
        write_project_lab_outputs(
            self._run_dir,
            result.report,
            external_inspirations=result.external_inspirations,
        )
        return format_project_lab_prompt_context(
            result.report,
            result.external_inspirations,
        )

    def __getattr__(self, name: str):
        return getattr(self._pipeline, name)


def _project_lab_is_needed(brief) -> bool:
    """Run the specialist only when the strategy needs new project evidence."""
    project_plan = getattr(brief, "project_plan", None)
    slots = getattr(project_plan, "slots", None)
    if slots is None:
        return True
    for slot in slots:
        mode = getattr(slot, "mode", None)
        if mode is None or mode in {"derive", "create"}:
            return True
        if bool(getattr(slot, "requires_external_inspiration", False)):
            return True
    return False
