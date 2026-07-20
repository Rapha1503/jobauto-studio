from pathlib import Path

import pytest

from jobauto.codex_client import GenerationPhase
from jobauto.latex_cv_source import analyze_latex_cv
from jobauto.profile_extraction import (
    CandidateProfileExtraction,
    CandidateProfileExtractor,
    ExtractedAdditionalSection,
    ExtractedEducation,
    ExtractedExperience,
    ExtractedIdentity,
    ExtractedProject,
    ExtractedSkill,
)


def _fixture() -> tuple[bytes, object]:
    path = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = path.read_bytes()
    return source, analyze_latex_cv(source, filename=path.name)


def _extraction(*, project_source: str = "projects") -> CandidateProfileExtraction:
    return CandidateProfileExtraction(
        locale="fr-FR",
        identity=ExtractedIdentity(
            first_name="Camille",
            last_name="Martin",
            email="camille.martin@example.test",
            phone="+33 1 00 00 00 01",
            location="Lyon",
            headline="Ingénieure systèmes",
            source_block_ids=["identity"],
        ),
        summary="Profil données et systèmes énergétiques.",
        summary_source_block_ids=["summary"],
        experiences=[
            ExtractedExperience(
                experience_id="gridlab",
                organization="GridLab",
                role="Ingénieure données",
                dates="2024--2026",
                tools=["Python", "SQL"],
                facts=["Développement de pipelines pour des données énergétiques."],
                source_block_ids=["experience"],
            )
        ],
        projects=[
            ExtractedProject(
                project_id="energy_forecasting",
                title="Prévision de demande énergétique",
                stack=["Python", "scikit-learn"],
                description=["Modélisation de séries temporelles."],
                source_block_ids=[project_source],
            )
        ],
        skills=[
            ExtractedSkill(
                name="Python",
                category="Data",
                source_block_ids=["skills"],
            )
        ],
        education=[
            ExtractedEducation(
                institution="École du numérique",
                program="Diplôme d'ingénieur",
                source_block_ids=["education"],
            )
        ],
        languages=["Français natif", "Anglais C1"],
        interests=["Énergie", "Lecture"],
    )


class FakeClient:
    def __init__(self, result: CandidateProfileExtraction) -> None:
        self.result = result
        self.prompt = ""
        self.phase = None

    def complete_json(self, prompt, response_model, phase):
        self.prompt = prompt
        self.phase = phase
        assert response_model is CandidateProfileExtraction
        return self.result


def test_extractor_uses_only_confirmed_content_blocks_and_preserves_unicode() -> None:
    source, mapping = _fixture()
    client = FakeClient(_extraction())

    result = CandidateProfileExtractor(client).extract(source, mapping)

    assert result.identity.first_name == "Camille"
    assert result.locale == "fr-FR"
    assert result.projects[0].title == "Prévision de demande énergétique"
    assert client.phase is GenerationPhase.PROFILE
    assert "Prévision de demande énergétique" in client.prompt
    assert "\\documentclass" not in client.prompt
    assert "\\usepackage" not in client.prompt
    assert "privatecandidate" not in client.prompt.casefold()
    assert "privateemployer" not in client.prompt.casefold()
    assert "primary natural-language locale" in client.prompt
    assert "without shortening or removing a metric" in client.prompt


def test_pdf_extractor_uses_page_provenance_without_latex_assumptions() -> None:
    extraction = CandidateProfileExtraction(
        locale="en-GB",
        identity=ExtractedIdentity(
            first_name="Alex",
            last_name="Morgan",
            email="alex.morgan@example.test",
            source_block_ids=["page_1"],
        ),
    )
    client = FakeClient(extraction)

    result = CandidateProfileExtractor(client).extract_pdf_pages(
        ["Alex Morgan\nalex.morgan@example.test\nRegulatory affairs specialist"]
    )

    assert result.identity.first_name == "Alex"
    assert client.phase is GenerationPhase.PROFILE
    assert "PDF_PAGES_JSON" in client.prompt
    assert '"block_id": "page_1"' in client.prompt
    assert "Do not adapt the profile to a job" in client.prompt


def test_extractor_restores_complete_source_bullets_when_agent_shortens_a_metric() -> None:
    source, mapping = _fixture()
    extraction = _extraction()
    experience = extraction.experiences[0].model_copy(
        update={
            "facts": [
                "Développement de pipelines pour des données énergétiques.",
                "Contrôles qualité et supervision des traitements.",
            ],
            "metrics": ["Python et SQL"],
        }
    )
    extraction = extraction.model_copy(update={"experiences": [experience]})

    result = CandidateProfileExtractor(FakeClient(extraction)).extract(source, mapping)

    assert result.experiences[0].facts == [
        "Développement de pipelines Python et SQL pour des données énergétiques.",
        "Contrôles qualité et supervision des traitements.",
    ]


def test_extractor_restores_bullets_for_multiple_experiences_in_one_source_block() -> None:
    path = Path(__file__).parent / "fixtures" / "cv" / "exhibition_producer_en.tex"
    source = path.read_bytes()
    mapping = analyze_latex_cv(source, filename=path.name)
    extraction = CandidateProfileExtraction(
        locale="en-GB",
        identity=ExtractedIdentity(
            first_name="Maya",
            last_name="Laurent",
            email="maya.laurent@example.test",
            source_block_ids=["identity"],
        ),
        experiences=[
            ExtractedExperience(
                experience_id="atelier",
                organization="Atelier Horizon",
                role="Exhibition Producer",
                facts=[
                    "Produced six temporary exhibitions from initial brief to opening.",
                    "Managed production schedules, supplier consultations and budgets.",
                    "Prepared installation plans and post-event reports.",
                ],
                metrics=["Budgets up to EUR 180,000"],
                source_block_ids=["experience"],
            ),
            ExtractedExperience(
                experience_id="cultures",
                organization="Maison des Cultures",
                role="Programme Coordinator",
                facts=[
                    "Coordinated a year-round programme across three venues.",
                    "Built shared production calendars and audience feedback summaries.",
                ],
                source_block_ids=["experience"],
            ),
        ],
        additional_sections=[
            ExtractedAdditionalSection(
                label=block.label,
                content="Candidate-owned supporting evidence",
                source_block_ids=[block.block_id],
            )
            for block in mapping.blocks
            if block.kind.value == "other"
        ],
    )

    result = CandidateProfileExtractor(FakeClient(extraction)).extract(source, mapping)

    assert "budgets up to EUR 180,000" in result.experiences[0].facts[1]
    assert result.experiences[1].facts[0].startswith("Coordinated a year-round programme")
    assert len(result.experiences[0].facts) == 3
    assert len(result.experiences[1].facts) == 2


def test_extractor_rejects_foreign_or_invented_source_block_id() -> None:
    source, mapping = _fixture()

    with pytest.raises(ValueError, match="unknown CV blocks"):
        CandidateProfileExtractor(FakeClient(_extraction(project_source="foreign"))).extract(
            source, mapping
        )


def test_extractor_rejects_source_changed_after_mapping() -> None:
    source, mapping = _fixture()

    with pytest.raises(ValueError, match="changed after mapping"):
        CandidateProfileExtractor(FakeClient(_extraction())).extract(source + b"% changed", mapping)


def test_extractor_preserves_arbitrary_named_sections_with_provenance() -> None:
    path = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = path.read_bytes().replace(
        b"\\end{document}",
        b"\\cvsection{Certifications}\nISO 13485 Internal Auditor\n"
        b"\\cvsection{Professional Memberships}\n"
        b"Regulatory Affairs Professionals Society\n\\end{document}",
    )
    mapping = analyze_latex_cv(source, filename=path.name)
    extraction = CandidateProfileExtraction(
        identity=ExtractedIdentity(
            first_name="Sofia",
            last_name="Martin",
            email="sofia.martin@example.test",
            source_block_ids=["identity"],
        ),
        additional_sections=[
            ExtractedAdditionalSection(
                label="Certifications",
                content="ISO 13485 Internal Auditor",
                source_block_ids=["other"],
            ),
            ExtractedAdditionalSection(
                label="Professional Memberships",
                content="Regulatory Affairs Professionals Society",
                source_block_ids=["other-2"],
            ),
        ],
    )
    client = FakeClient(extraction)

    result = CandidateProfileExtractor(client).extract(source, mapping)

    assert [item.label for item in result.additional_sections] == [
        "Certifications",
        "Professional Memberships",
    ]
    assert "every supplied block whose kind is other" in client.prompt


def test_extractor_rejects_a_silently_dropped_custom_section() -> None:
    path = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = path.read_bytes().replace(
        b"\\end{document}",
        b"\\cvsection{Publications}\nOne peer-reviewed article\n\\end{document}",
    )
    mapping = analyze_latex_cv(source, filename=path.name)

    with pytest.raises(ValueError, match="preserve every additional CV section"):
        CandidateProfileExtractor(FakeClient(_extraction())).extract(source, mapping)
