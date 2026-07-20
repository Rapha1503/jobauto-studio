from __future__ import annotations

import base64
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from jobauto.application_service import RunApplicationService
from jobauto.candidate_pipeline import ApplicationRow
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.document_patch import (
    CandidateDocumentDraft,
    CvAdaptationPatch,
    CvFieldChange,
    apply_cv_patch,
)
from jobauto.excel_schema import TRACKER_COLUMNS
from jobauto.models import (
    ApplicationBrief,
    CandidateApplicationReview,
    CandidateLetterDraft,
    LetterArgumentAssessment,
    LetterArgumentCriterionAssessment,
)
from jobauto.profile_extraction import CandidateProfileExtraction
from jobauto.public_validation import extract_pdf_text
from jobauto.run_store import RunStore
from jobauto.source_preserving_cv import LatexBlockReplacement, LatexCvPatch
from jobauto.studio.app import create_studio_app


def _passing_letter_argument() -> LetterArgumentAssessment:
    def criterion() -> LetterArgumentCriterionAssessment:
        return LetterArgumentCriterionAssessment(
            state="pass",
            rationale="The rendered letter provides concrete support for this criterion.",
            supporting_excerpt="I am applying for the",
        )

    return LetterArgumentAssessment(
        target_specificity=criterion(),
        evidence_to_missions=criterion(),
        candidate_contribution=criterion(),
        motivation_credibility=criterion(),
        tone_and_naturalness=criterion(),
    )


class CulturalProfileExtractor:
    def extract(self, _source: bytes, _mapping) -> CandidateProfileExtraction:
        return CandidateProfileExtraction.model_validate(
            {
                "identity": {
                    "first_name": "Maya",
                    "last_name": "Laurent",
                    "email": "maya.laurent@example.test",
                    "phone": "+33 1 00 00 00 02",
                    "location": "Paris",
                    "headline": "Exhibition Producer | Cultural Programmes | Paris",
                    "source_block_ids": ["identity"],
                },
                "summary": (
                    "Exhibition producer with four years of experience coordinating cultural "
                    "programmes, touring installations and public events."
                ),
                "summary_source_block_ids": ["summary"],
                "experiences": [
                    {
                        "experience_id": "atelier_horizon",
                        "organization": "Atelier Horizon",
                        "role": "Exhibition Producer",
                        "dates": "2023 - 2026",
                        "tools": [],
                        "facts": [
                            "Produced six temporary exhibitions from brief to opening.",
                            "Managed schedules, suppliers and budgets up to EUR 180,000.",
                        ],
                        "metrics": ["Six exhibitions", "EUR 180,000 production budgets"],
                        "source_block_ids": ["experience"],
                    },
                    {
                        "experience_id": "maison_cultures",
                        "organization": "Maison des Cultures",
                        "role": "Programme Coordinator",
                        "dates": "2021 - 2023",
                        "tools": [],
                        "facts": [
                            "Coordinated more than 70 public events across three venues.",
                            "Improved handovers between programming, technical and communications teams.",
                        ],
                        "metrics": ["More than 70 events", "Three venues"],
                        "source_block_ids": ["experience"],
                    },
                ],
                "projects": [],
                "skills": [
                    {
                        "name": name,
                        "category": category,
                        "source_block_ids": ["other-2"],
                    }
                    for name, category in (
                        ("Production planning", "Production"),
                        ("Budget control", "Production"),
                        ("Supplier coordination", "Partnerships"),
                        ("Visitor experience", "Programme Delivery"),
                        ("Accessibility", "Programme Delivery"),
                        ("Stakeholder facilitation", "Communication"),
                    )
                ],
                "education": [
                    {
                        "institution": "University of Paris",
                        "program": "MA Cultural Project Management",
                        "dates": "2019 - 2021",
                        "details": [
                            "Cultural policy, production economics and audience development."
                        ],
                        "source_block_ids": ["education"],
                    }
                ],
                "languages": ["French native", "English C1", "Spanish B2"],
                "interests": ["Contemporary art", "Community programmes"],
            }
        )


class CulturalDocumentPipeline:
    def __init__(self, snapshot) -> None:
        self.snapshot = snapshot

    def generate_candidate_documents(self, row: ApplicationRow, _offer_text: str, **_kwargs):
        adapted_summary = (
            "Exhibition producer experienced in delivering touring installations and public "
            "programmes, coordinating artists, venues, suppliers, budgets and accessible visitor "
            "experiences from brief to opening."
        )
        patch = CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="summary.text",
                    value=adapted_summary,
                    fact_ids=["profile.summary"],
                )
            ]
        )
        semantic = apply_cv_patch(self.snapshot, patch)
        mapping = self.snapshot.cv_mapping
        assert mapping is not None
        summary = next(block for block in mapping.blocks if block.block_id == "summary")
        latex_patch = LatexCvPatch(
            replacements=[
                LatexBlockReplacement(
                    block_id=summary.block_id,
                    source_ids=["summary.text"],
                    latex=f"\\cvsection{{{summary.label}}}\n{adapted_summary}\n",
                )
            ]
        )
        cv = type(semantic)(
            document=semantic.document,
            provenance=semantic.provenance,
            latex_patch=latex_patch,
            source_blocks=semantic.source_blocks,
        )
        letter = CandidateLetterDraft(
            greeting="Dear hiring team,",
            paragraphs=[
                (
                    f"I am applying for the {row.role} role at {row.company}. With four years "
                    "of experience coordinating exhibitions, touring installations and public "
                    "programmes, I am interested in contributing to a team that connects strong "
                    "cultural content with reliable delivery and an accessible visitor experience."
                ),
                (
                    "At Atelier Horizon, I have produced six temporary exhibitions from the first "
                    "brief through installation and opening. The work combines production schedules, "
                    "supplier consultations, budgets of up to EUR 180,000 and close coordination "
                    "with artists, curators, fabricators, venues and accessibility partners. This "
                    "has taught me to keep creative, operational and public-facing priorities aligned "
                    "while making risks and decisions visible to every contributor."
                ),
                (
                    "I have also coordinated public programmes across several venues and delivered "
                    "touring and evening formats requiring transport, timed access, mediation teams "
                    "and partner communications. These experiences match the role's emphasis on "
                    "installation logistics, stakeholder coordination and visitor-facing programmes, "
                    "and would allow me to contribute quickly without losing sight of the purpose "
                    "behind each production."
                ),
                (
                    "I would bring structured production management, clear communication and a "
                    "practical understanding of how exhibitions move from an ambitious concept to "
                    "a safe, accessible and well-coordinated opening. I would be pleased to discuss "
                    "how this experience could support your upcoming programme."
                ),
            ],
            closing="Kind regards,\nMaya Laurent",
            used_fact_ids=["profile.summary", "source_block.experience"],
        ).validate_for_snapshot(self.snapshot)
        return CandidateDocumentDraft(
            brief=ApplicationBrief.model_construct(
                company=row.company,
                role=row.role,
                language="en",
                requirements=[],
            ),
            cv_patch=patch,
            cv=cv,
            letter=letter,
        )

    def review_candidate_documents(
        self, _row, _package, cv_rendered, letter_rendered, _offer_text
    ) -> CandidateApplicationReview:
        assert cv_rendered.page_count == letter_rendered.page_count == 1
        return CandidateApplicationReview(
            approved=True,
            score=92,
            ats_score=90,
            editorial_score=93,
            adaptation_score=92,
            blocking_issues=[],
            warnings=[],
            letter_argument=_passing_letter_argument(),
            requirement_coverage=[],
        )

    def repair_candidate_documents(self, *_args, **_kwargs):
        raise AssertionError("The non-technical public happy path must not require repair")


class CulturalDiscoveryAgent:
    def __init__(self) -> None:
        self.prompt = ""

    def complete_json(self, prompt, response_model, _phase):
        self.prompt = prompt
        offer = (
            "Permanent Exhibition Producer role in Paris. Lead exhibition schedules, budgets, "
            "procurement, artist and venue coordination, installation logistics, accessibility "
            "and visitor-facing public programmes from brief to opening."
        )
        return response_model.model_validate(
            {
                "offers": [
                    {
                        "company": "Museum Forum",
                        "role": "Exhibition Producer",
                        "url": "https://example.test/jobs/exhibition-producer",
                        "description": offer,
                        "location": "Paris, France",
                        "contract_type": "permanent",
                        "posted_at": date.today().isoformat(),
                        "semantic_fit_score": 96,
                        "semantic_fit_rationale": (
                            "The central production responsibilities match the candidate's evidence."
                        ),
                    },
                    {
                        "company": "Code Factory",
                        "role": "Data Engineer",
                        "url": "https://example.test/jobs/data-engineer",
                        "description": (
                            "Permanent Data Engineer role in Paris requiring Python, SQL, Spark "
                            "and cloud data-platform experience. Build batch and streaming pipelines, "
                            "maintain production data quality, support analytics users, document "
                            "technical decisions and collaborate with software and infrastructure teams."
                        ),
                        "location": "Paris, France",
                        "contract_type": "permanent",
                        "posted_at": date.today().isoformat(),
                        "semantic_fit_score": 8,
                        "semantic_fit_rationale": (
                            "The central software function is outside the configured cultural roles."
                        ),
                    },
                ],
                "rejected_candidates": [],
                "notes": "Synthetic cross-domain discovery batch.",
                "END_OF_RESPONSE": True,
            }
        )


def _tracker(path: Path) -> Path:
    workbook = Workbook()
    try:
        sheet = workbook.active
        sheet.title = "Postulations"
        for column, header in enumerate(TRACKER_COLUMNS, start=1):
            sheet.cell(1, column).value = header
        workbook.save(path)
    finally:
        workbook.close()
    return path


def test_nontechnical_tex_crosses_public_studio_routes_to_verified_sandbox(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    state = tmp_path / "state"
    profiles = state / "candidate-profiles"
    profiles.mkdir(parents=True)
    application = RunApplicationService(
        repository=CandidateProfileRepository(profiles),
        store=RunStore(state / "runs"),
        pipeline_factory=lambda snapshot, _context: CulturalDocumentPipeline(snapshot),
    )
    discovery_agent = CulturalDiscoveryAgent()
    client = TestClient(
        create_studio_app(
            project_root=project_root,
            state_root=state,
            profile_extractor=CulturalProfileExtractor(),
            application_service=application,
            discovery_agent_factory=lambda _callback: discovery_agent,
        )
    )
    fixture = project_root / "tests" / "fixtures" / "cv" / "exhibition_producer_en.tex"

    imported = client.post(
        "/api/tex-imports",
        content=fixture.read_bytes(),
        headers={"X-Filename": fixture.name},
    )
    assert imported.status_code == 201
    import_id = imported.json()["import_id"]
    started = client.post(f"/api/tex-imports/{import_id}/profile-draft")
    extraction = client.get(started.json()["status_url"]).json()
    assert extraction["status"] == "completed"

    draft_url = f"/api/candidate-drafts/{extraction['draft_id']}"
    draft = client.get(draft_url).json()
    update = {
        key: draft[key]
        for key in (
            "identity",
            "locale",
            "summary",
            "summary_source_block_ids",
            "letter_reference",
            "experiences",
            "skills",
            "projects",
            "education",
            "languages",
            "interests",
            "cv_layout",
            "project_lab",
            "search_preferences",
            "submission_preferences",
        )
    }
    update["expected_version"] = draft["version"]
    update["letter_reference"] = "Dear hiring team, I am applying for this opportunity."
    update["search_preferences"]["roles"]["preferred"] = [
        "Exhibition Producer",
        "Cultural Programme Producer",
    ]
    update["search_preferences"]["announcement_keywords"]["preferred"] = [
        "exhibitions",
        "cultural programmes",
        "installation",
        "artist coordination",
    ]
    update["search_preferences"]["locations"]["preferred"] = ["Paris, France"]
    update["search_preferences"]["contracts"]["required"] = ["permanent"]
    update["search_preferences"]["sectors"]["preferred"] = [
        "museums",
        "cultural institutions",
    ]
    saved = client.put(draft_url, json=update)
    assert saved.status_code == 200
    assert client.post(f"{draft_url}/validate").status_code == 200
    exported = client.post(f"{draft_url}/export")
    assert exported.status_code == 201
    candidate_id = exported.json()["candidate_id"]

    preview = client.get(exported.json()["preview_url"])
    assert preview.status_code == 200
    preview_path = tmp_path / "original-preview.pdf"
    preview_path.write_bytes(preview.content)
    preview_text = extract_pdf_text(preview_path)
    assert "Selected Productions" in preview_text
    assert "Grants & Awards" in preview_text
    assert "Professional Memberships" in preview_text
    assert "Community Engagement" in preview_text
    assert "Projects" not in preview_text
    assert "Python" not in preview_text

    tracker = _tracker(tmp_path / "applications.xlsx")
    discovery = client.post(
        f"/profiles/{candidate_id}/discoveries",
        json={
            "tracker_path": str(tracker),
            "requested_count": 1,
            "conversation_url": "https://chatgpt.com/c/jobauto-cultural-demo",
        },
    )
    assert discovery.status_code == 201
    discovery_status = client.get(discovery.json()["status_url"]).json()
    assert discovery_status["status"] == "campaign_created"
    assert "Exhibition Producer" in discovery_agent.prompt
    assert "cultural programmes" in discovery_agent.prompt
    campaign_id = discovery_status["campaign_id"]
    campaign_status = client.get(f"/campaigns/{campaign_id}/status").json()
    assert campaign_status["status"] == "completed"
    assert campaign_status["completed_count"] == 1
    assert campaign_status["selected_count"] == 1
    assert campaign_status["items"][0]["offer"]["role"] == "Exhibition Producer"
    assert campaign_status["items"][1]["decision"] != "selected"
    assert campaign_status["items"][1]["offer"]["role"] == "Data Engineer"
    run_id = campaign_status["items"][0]["run_id"]
    run = application.get(run_id)
    cv_path = Path(str(run.artifacts["cv"]["pdf_path"]))
    letter_path = Path(str(run.artifacts["letter"]["pdf_path"]))
    cv_text = extract_pdf_text(cv_path)
    assert "artists, venues, suppliers, budgets" in cv_text
    assert "Selected Productions" in cv_text
    assert "Grants & Awards" in cv_text
    assert "Professional Memberships" in cv_text
    assert "Community Engagement" in cv_text
    assert "Projects" not in cv_text
    assert "Python" not in cv_text
    assert run.artifacts["cv"]["page_count"] == 1
    cv_layout = run.artifacts["cv"]["layout_metrics"]
    assert cv_layout["font_size_pt"] == 12.0
    assert cv_layout["line_height_ratio"] >= 1.2
    assert cv_layout["section_spacing_pt"] > 0
    assert cv_layout["vertical_coverage_ratio"] >= 0.85
    assert cv_layout["requires_density_review"] is False
    assert run.artifacts["letter"]["page_count"] == 1
    assert int(run.artifacts["letter"]["extracted_text_characters"]) > 1_200

    submission = client.post(f"/campaigns/{campaign_id}/submission")
    assert submission.status_code == 201
    claimed = client.post(f"/campaigns/{campaign_id}/submission/claim-next").json()
    handoff = claimed["packet"]
    assert handoff["status"] == "claimed_for_chrome"
    receipt = client.post(
        f"/sandbox/apply/{handoff['handoff_id']}/submit",
        json={
            "full_name": "Maya Laurent",
            "email": "maya.laurent@example.test",
            "location": "Paris",
            "message": "I am interested in this exhibition production role.",
            "cv": {
                "name": cv_path.name,
                "content_base64": base64.b64encode(cv_path.read_bytes()).decode("ascii"),
            },
            "letter": {
                "name": letter_path.name,
                "content_base64": base64.b64encode(letter_path.read_bytes()).decode("ascii"),
            },
        },
    )
    assert receipt.status_code == 201
    assert receipt.json()["status"] == "sandbox_verified"
