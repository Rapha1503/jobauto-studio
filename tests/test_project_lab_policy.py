from jobauto.models import ProjectPlan, ProjectSlotPlan
from jobauto.project_lab_policy import MAX_VISIBLE_PROJECTS, ProjectLabPolicy


def test_project_count_is_candidate_configurable_within_one_page_limit() -> None:
    policy = ProjectLabPolicy(
        minimum_visible_projects=1,
        maximum_visible_projects=3,
    )

    assert policy.minimum_visible_projects == 1
    assert policy.maximum_visible_projects == 3


def test_project_plan_represents_the_configured_one_page_limit() -> None:
    slots = [
        ProjectSlotPlan(
            slot=index,
            mode="create",
            requirement_ids=[f"req.{index}"],
            rationale=f"Project {index} covers a distinct central requirement.",
        )
        for index in range(1, MAX_VISIBLE_PROJECTS + 1)
    ]

    plan = ProjectPlan(
        decision="create",
        rationale="The candidate explicitly configured distinct project slots.",
        central_gaps=[f"Central gap {index}" for index in range(1, MAX_VISIBLE_PROJECTS + 1)],
        slots=slots,
    )

    assert len(plan.slots) == MAX_VISIBLE_PROJECTS
