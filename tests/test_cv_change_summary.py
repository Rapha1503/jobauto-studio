from jobauto.cv_change_summary import compare_cv_documents
from jobauto.cv_source import CvEntry, CvSourceDocument


def test_cv_change_summary_groups_only_real_document_changes() -> None:
    original = CvSourceDocument(
        name="Jamie Chen",
        headline="Financial analyst | Excel | Paris",
        contact_line="jamie@example.test",
        summary="Financial analyst with reporting experience.",
        experience=[
            CvEntry(
                title="Example Co - Analyst",
                dates="2024-2026",
                bullets=["Built monthly reports.", "Worked with finance stakeholders."],
            )
        ],
        projects=[CvEntry(title="Forecast model", stack="Excel", bullets=["Built a forecast."])],
        skills={"Finance": ["Budget"], "Tools": ["Excel"]},
        languages="French C1 | English C1",
    )
    adapted = original.model_copy(
        update={
            "headline": "FP&A analyst | Excel, SQL, Power BI | Paris",
            "summary": "Financial analyst focused on FP&A, budgeting and decision-ready reporting.",
            "experience": [
                original.experience[0].model_copy(
                    update={
                        "bullets": [
                            "Built monthly performance and variance reports.",
                            "Worked with finance stakeholders.",
                        ]
                    }
                )
            ],
            "skills": {
                "FP&A": ["Budget", "Forecast"],
                "Data & reporting": ["Excel", "SQL", "Power BI"],
            },
        }
    )

    summary = compare_cv_documents(original, adapted)

    assert summary.change_count == 4
    assert summary.changed_sections == ["headline", "summary", "experience", "skills"]
    assert "Built monthly reports" in summary.items[2].before
    assert "variance reports" in summary.items[2].after
    assert all(item.section != "projects" for item in summary.items)
    assert all(item.section != "languages" for item in summary.items)
