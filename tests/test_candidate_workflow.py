from pathlib import Path
from types import SimpleNamespace

from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.candidate_workflow import CandidateWorkflowPipeline
from jobauto.models import ApplicationRow
from jobauto.project_lab import ProjectLabFamily
from jobauto.project_lab_policy import ProjectLabPolicy


def _snapshot():
    profiles = Path(__file__).resolve().parents[1] / "config" / "profiles"
    return CandidateProfileRepository(profiles).load_snapshot(profiles / "example" / "profile.yaml")


class StubDocumentPipeline:
    def __init__(self) -> None:
        self.context = None
        self.brief = SimpleNamespace(
            model_dump_json=lambda indent: '{\n  "brief": true\n}',
            project_plan=SimpleNamespace(slots=[object(), object(), object()]),
            baseline_cv_assessment=SimpleNamespace(decision="adapt"),
        )

    def generate_lean_brief(
        self,
        _row,
        _offer_text,
        *,
        project_lab_context="",
    ):
        assert project_lab_context == ""
        return self.brief

    def generate_candidate_documents(
        self,
        _row,
        _offer_text,
        *,
        brief=None,
        project_lab_context="",
    ):
        self.context = project_lab_context
        assert brief is self.brief
        return "documents"


class StubProjectLab:
    def __init__(self) -> None:
        self.call = None

    def suggest(self, row, offer_text, *, families, profile, cv_slot_budget):
        self.call = (row, offer_text, families, profile, cv_slot_budget)
        return SimpleNamespace(report=object(), external_inspirations=[])


def test_candidate_workflow_runs_and_persists_project_lab_before_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = _snapshot()
    documents = StubDocumentPipeline()
    project_lab = StubProjectLab()
    persisted = {}

    monkeypatch.setattr(
        "jobauto.candidate_workflow.write_project_lab_outputs",
        lambda run_dir, report, external_inspirations: persisted.update(
            run_dir=run_dir,
            report=report,
            inspirations=external_inspirations,
        ),
    )
    monkeypatch.setattr(
        "jobauto.candidate_workflow.format_project_lab_prompt_context",
        lambda _report, _inspirations: "## PROJECT LAB\nresolved",
    )
    workflow = CandidateWorkflowPipeline(
        pipeline=documents,
        project_lab=project_lab,
        snapshot=snapshot,
        run_dir=tmp_path,
    )
    row = ApplicationRow(
        excel_row=1,
        company="Example",
        role="Data Engineer",
        url="https://example.test/job",
    )

    result = workflow.generate_candidate_documents(
        row,
        "Example seeks a Data Engineer for reliable Python and SQL pipelines.",
    )

    assert result == "documents"
    assert project_lab.call[2] == [
        ProjectLabFamily.REAL_PROJECT,
        ProjectLabFamily.PERSONAL_PROJECT_INSPIRED,
        ProjectLabFamily.SYNTHETIC_PROJECT,
    ]
    assert project_lab.call[3] is documents.brief
    assert project_lab.call[4] == 3
    assert persisted["run_dir"] == tmp_path
    assert documents.context == "## PROJECT LAB\nresolved"
    assert (tmp_path / "application-brief.json").read_text(encoding="utf-8") == (
        '{\n  "brief": true\n}'
    )


def test_candidate_workflow_uses_the_strategy_slot_count_not_policy_maximum(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = SimpleNamespace(
        profile=SimpleNamespace(
            project_lab=ProjectLabPolicy(
                allow_new_project=True,
                minimum_visible_projects=1,
                maximum_visible_projects=3,
            )
        )
    )
    documents = StubDocumentPipeline()
    documents.brief.project_plan = SimpleNamespace(slots=[object(), object()])
    project_lab = StubProjectLab()
    monkeypatch.setattr(
        "jobauto.candidate_workflow.write_project_lab_outputs",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "jobauto.candidate_workflow.format_project_lab_prompt_context",
        lambda *_args, **_kwargs: "project context",
    )
    workflow = CandidateWorkflowPipeline(
        pipeline=documents,
        project_lab=project_lab,
        snapshot=snapshot,
        run_dir=tmp_path,
    )

    workflow.generate_candidate_documents(
        ApplicationRow(
            excel_row=2,
            company="Example",
            role="Example role",
            url="https://example.test/job-2",
        ),
        "Example role description.",
    )

    assert project_lab.call[4] == 2


def test_candidate_workflow_skips_project_lab_when_baseline_cv_is_kept(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    documents = StubDocumentPipeline()
    documents.brief.baseline_cv_assessment = SimpleNamespace(decision="keep_baseline")
    project_lab = StubProjectLab()
    workflow = CandidateWorkflowPipeline(
        pipeline=documents,
        project_lab=project_lab,
        snapshot=snapshot,
        run_dir=tmp_path,
    )

    result = workflow.generate_candidate_documents(
        ApplicationRow(
            excel_row=3,
            company="Example",
            role="Data Engineer",
            url="https://example.test/job-3",
        ),
        "Example seeks a Data Engineer for reliable Python and SQL pipelines.",
    )

    assert result == "documents"
    assert project_lab.call is None
    assert documents.context == ""
