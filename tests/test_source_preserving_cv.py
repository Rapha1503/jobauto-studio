from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from test_candidate_export import _validated_draft
from test_candidate_writers import _brief, _letter_argument

from jobauto.adaptation_policy import FidelityLevel, SectionPolicy
from jobauto.candidate_context import CandidateContext
from jobauto.candidate_export import export_candidate_draft
from jobauto.candidate_pipeline import CandidatePipeline
from jobauto.codex_client import GenerationPhase
from jobauto.cv_source import CvEntry
from jobauto.document_patch import (
    CvAdaptationPatch,
    CvFieldChange,
    CvProjectSectionChange,
    CvSourceBlockChange,
    apply_cv_patch,
    source_preserving_item_groups,
    validate_cv_document,
)
from jobauto.document_renderer import DocumentRenderer, source_skill_line_budget
from jobauto.latex_cv_source import LatexCvMapping
from jobauto.models import (
    ApplicationRow,
    CandidateApplicationReview,
    CandidateLetterDraft,
    CandidateRepairAction,
    ProjectPlan,
    SkillPlan,
)
from jobauto.source_preserving_cv import (
    LatexBlockReplacement,
    LatexCvPatch,
    _require_safe_structure,
    latex_cv_prompt_blocks,
    merge_latex_cv_patch,
    render_source_preserving_cv,
    validate_latex_cv_patch,
)


def _source_snapshot(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes()
    draft, mapping = _validated_draft(source, fixture.name)
    _, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    return source, snapshot


def _two_group_source_snapshot(tmp_path: Path):
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    item_indexes = [index for index, line in enumerate(lines) if line.startswith("\\item ")]
    second_experience_item = item_indexes[1]
    lines[second_experience_item:second_experience_item] = [
        "\\end{itemize}",
        "\\textbf{Internal quality initiative}",
        "\\begin{itemize}[leftmargin=*]",
    ]
    source = ("\n".join(lines) + "\n").encode("utf-8")
    draft, mapping = _validated_draft(source, "two_group_cv.tex")
    experience_items = [
        line.removeprefix("\\item ") for line in lines if line.startswith("\\item ")
    ][:2]
    experience = draft.experiences[0].model_copy(update={"facts": experience_items})
    draft = draft.model_copy(update={"experiences": [experience]})
    _, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "two-group-profiles",
    )
    return snapshot


def test_source_preserving_experience_facts_cannot_cross_visible_heading_groups(
    tmp_path: Path,
) -> None:
    snapshot = _two_group_source_snapshot(tmp_path)

    assert source_preserving_item_groups(snapshot, "experience") == (
        ("experience.0.bullet.0",),
        ("experience.0.bullet.1",),
    )
    with pytest.raises(ValueError, match="crosses source heading groups"):
        apply_cv_patch(
            snapshot,
            CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="experience.0.bullet.0",
                        value="Reframed quality-control contribution.",
                        fact_ids=["experience.gridlab.fact.2"],
                    )
                ]
            ),
        )
    pipeline = CandidatePipeline.for_candidate(
        _SourcePreservingLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    prompt = pipeline._candidate_cv_patch_prompt(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        _brief_with_snapshot_skill_shape(snapshot),
        "GridCo recherche une Data Engineer.",
    )
    assert "SOURCE-PRESERVING EXPERIENCE GROUPS" in prompt
    assert "never move a fact under another heading" in prompt


def _adapted_summary(snapshot):
    return apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="summary.text",
                    value=(
                        "Ingénieure data spécialisée dans les pipelines Python et SQL fiables "
                        "pour les systèmes énergétiques."
                    ),
                    fact_ids=["profile.summary"],
                )
            ]
        ),
    )


def _brief_with_snapshot_skill_shape(snapshot):
    brief = _brief()
    category = next(iter(snapshot.cv_source.skills))
    return brief.model_copy(
        update={
            "skill_plan": brief.skill_plan.model_copy(
                update={
                    "categories": [category],
                    "items": [
                        item.model_copy(update={"category": category})
                        for item in brief.skill_plan.items
                    ],
                }
            )
        }
    )


def _summary_latex_patch() -> LatexCvPatch:
    return LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="summary",
                source_ids=["summary.text"],
                latex=(
                    "\\cvsection{Résumé}\n"
                    "Ingénieure data spécialisée dans les pipelines Python et SQL fiables "
                    "pour les systèmes énergétiques.\n"
                ),
            )
        ]
    )


def test_source_preserving_project_patch_fits_content_to_existing_bullet_shape(
    tmp_path: Path,
) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    shaped_blocks = [
        block.model_copy(update={"policy": SectionPolicy(fidelity=FidelityLevel.ADAPTABLE)})
        if block.block_id == "projects"
        else block
        for block in mapping.blocks
    ]
    snapshot = replace(
        snapshot,
        _cv_mapping=LatexCvMapping.model_validate(
            {**mapping.model_dump(mode="json"), "blocks": shaped_blocks}
        ),
    )
    project = snapshot.cv_source.projects[0]
    patch = CvAdaptationPatch(
        projects=CvProjectSectionChange(
            entries=[
                CvEntry(
                    title=project.title,
                    dates=project.dates,
                    stack=project.stack,
                    bullets=[
                        "Premier fait vérifié.",
                        "Deuxième fait vérifié.",
                        "Troisième fait vérifié.",
                    ],
                )
            ],
            fact_ids=["project.energy_forecasting"],
        )
    )

    draft = apply_cv_patch(snapshot, patch)

    assert draft.document.projects[0].bullets == [
        "Premier fait vérifié. Deuxième fait vérifié. Troisième fait vérifié."
    ]


def test_source_preserving_renderer_keeps_exact_preamble_and_compiles_adapted_cv(
    tmp_path: Path,
) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    semantic = _adapted_summary(snapshot)
    draft = type(semantic)(
        document=semantic.document,
        provenance=semantic.provenance,
        latex_patch=_summary_latex_patch(),
    )

    rendered = DocumentRenderer().render_cv(snapshot, draft, tmp_path / "rendered")

    mapping = snapshot.cv_mapping
    assert mapping is not None
    rendered_source = rendered.source_path.read_bytes()
    assert rendered_source[: mapping.preamble_end_byte] == source[: mapping.preamble_end_byte]
    assert rendered_source.count(b"% JOBAUTO_PDF_TEXT_MAPPING") == 1
    assert b"\\pdfgentounicode=1" in rendered_source
    assert rendered.page_count == 1
    assert "pipelines Python et SQL fiables" in rendered.extracted_text
    assert "camille.martin@example.test" in rendered.extracted_text


def test_candidate_defined_section_is_first_class_and_rendered_with_provenance(
    tmp_path: Path,
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "cv" / "synthetic_cv_fr.tex"
    source = fixture.read_bytes().replace(
        b"\\end{document}",
        b"\\cvsection{Certifications}\n"
        b"ISO 13485 Internal Auditor | Good Clinical Practice\n"
        b"\\end{document}",
    )
    draft, mapping = _validated_draft(source, fixture.name)
    _, snapshot = export_candidate_draft(
        draft=draft,
        tex_source=source,
        mapping=mapping,
        profiles_root=tmp_path / "profiles",
    )
    custom = next(block for block in mapping.blocks if block.block_id == "other")
    adapted_text = "ISO 13485 internal-audit certification | Good Clinical Practice training"
    semantic = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            source_blocks=[
                CvSourceBlockChange(
                    source_id="source_block.other",
                    value=adapted_text,
                    fact_ids=["source_block.other"],
                )
            ]
        ),
    )
    original = source[custom.start_byte : custom.end_byte].decode("utf-8")
    replacement = original.replace(
        "ISO 13485 Internal Auditor | Good Clinical Practice",
        adapted_text,
    )
    latex_patch = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id=custom.block_id,
                source_ids=["source_block.other"],
                latex=replacement,
            )
        ]
    )
    rendered = DocumentRenderer().render_cv(
        snapshot,
        replace(semantic, latex_patch=latex_patch),
        tmp_path / "custom-section",
    )

    assert semantic.source_blocks == {"source_block.other": adapted_text}
    assert semantic.provenance["source_block.other"] == ("source_block.other",)
    prompt_block = next(
        block
        for block in latex_cv_prompt_blocks(
            snapshot,
            tuple(semantic.provenance),
            semantic.source_blocks,
        )
        if block["block_id"] == custom.block_id
    )
    assert prompt_block["adapted_visible_text"] == adapted_text
    pipeline = CandidatePipeline.for_candidate(
        object(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    writer_prompt = pipeline._candidate_cv_patch_prompt(
        ApplicationRow(
            excel_row=1,
            company="MedNova",
            role="Regulatory Affairs Specialist",
            url="https://example.test/jobs/regulatory-affairs",
        ),
        _brief(),
        "MedNova seeks regulatory documentation and quality-system experience.",
    )
    assert "source_block.other" in writer_prompt
    assert '"value_kind": "source_block_text"' in writer_prompt
    assert "without LaTeX commands or a rewritten section title" in writer_prompt
    assert rendered.page_count == 1
    assert adapted_text in " ".join(rendered.extracted_text.split())

    duplicated_patch = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id=custom.block_id,
                source_ids=["source_block.other"],
                latex=original.replace(
                    "ISO 13485 Internal Auditor | Good Clinical Practice",
                    "ISO 13485 Internal Auditor | Good Clinical Practice | " + adapted_text,
                ),
            )
        ]
    )
    with pytest.raises(ValueError, match="does not exactly match"):
        render_source_preserving_cv(
            snapshot,
            duplicated_patch,
            semantic.provenance,
            semantic.document,
            semantic.source_blocks,
        )

    with pytest.raises(ValueError, match="source block is blank"):
        validate_cv_document(
            snapshot,
            replace(semantic, source_blocks={"source_block.other": ""}),
        )


def test_source_preserving_patch_rejects_unmapped_or_dangerous_changes(
    tmp_path: Path,
) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    semantic = _adapted_summary(snapshot)

    with pytest.raises(ValueError, match="forbidden command"):
        validate_latex_cv_patch(
            snapshot,
            LatexCvPatch(
                replacements=[
                    LatexBlockReplacement(
                        block_id="summary",
                        source_ids=["summary.text"],
                        latex="\\cvsection{Résumé}\n\\input{foreign.tex}\n",
                    )
                ]
            ),
            semantic.provenance,
            semantic.document,
        )

    with pytest.raises(ValueError, match="unescaped special character"):
        validate_latex_cv_patch(
            snapshot,
            LatexCvPatch(
                replacements=[
                    LatexBlockReplacement(
                        block_id="summary",
                        source_ids=["summary.text"],
                        latex="\\cvsection{Résumé}\nData & AI.\n",
                    )
                ]
            ),
            semantic.provenance,
            semantic.document,
        )

    with pytest.raises(ValueError, match="Unknown semantic CV source ID"):
        render_source_preserving_cv(
            snapshot,
            LatexCvPatch(
                replacements=[
                    LatexBlockReplacement(
                        block_id="summary",
                        source_ids=["projects.section"],
                        latex="\\cvsection{Résumé}\nTexte.\n",
                    )
                ]
            ),
            semantic.provenance,
            semantic.document,
        )


def test_source_preserving_patch_rejects_changed_section_header(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    semantic = _adapted_summary(snapshot)
    patch = _summary_latex_patch().model_copy(deep=True)
    patch.replacements[0].latex = "\\section*{Résumé}\nTexte différent.\n"

    with pytest.raises(ValueError, match="(?:command structure|section header) changed"):
        validate_latex_cv_patch(snapshot, patch, semantic.provenance, semantic.document)


def test_source_preserving_patch_rejects_empty_visible_formatting(tmp_path: Path) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    block = next(item for item in mapping.blocks if item.block_id == "experience")
    original = source[block.start_byte : block.end_byte].decode("utf-8")

    with pytest.raises(ValueError, match="empty visible formatting"):
        _require_safe_structure(original, original + "\\textbf{}", block)


def test_faithful_block_allows_equivalent_inline_glyph_typography(tmp_path: Path) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    block = next(item for item in mapping.blocks if item.block_id == "experience")
    original = source[block.start_byte : block.end_byte].decode("utf-8")
    replacement = original.replace("\\,", " ").replace("\\texteuro{}", "€")

    _require_safe_structure(original, replacement, block)


def test_faithful_block_treats_eurosym_as_visible_typography(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    block = next(item for item in mapping.blocks if item.block_id == "experience")

    _require_safe_structure(
        r"\item Generated \euro{}35M in savings.",
        r"\item Generated €35M in savings.",
        block,
    )


def test_faithful_block_rejects_removed_currency_glyph(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    block = next(item for item in mapping.blocks if item.block_id == "experience")

    with pytest.raises(ValueError, match="changed visible glyph"):
        _require_safe_structure(
            r"\item Generated \euro{}35M in savings.",
            r"\item Generated 35M in savings.",
            block,
        )


def test_faithful_block_still_rejects_removed_list_structure(tmp_path: Path) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    block = next(item for item in mapping.blocks if item.block_id == "experience")
    original = source[block.start_byte : block.end_byte].decode("utf-8")
    replacement = original.replace("\\item", "", 1)

    with pytest.raises(ValueError, match="expected_commands=.*actual_commands"):
        _require_safe_structure(original, replacement, block)


def test_source_preserving_patch_rejects_claim_added_only_during_latex_projection(
    tmp_path: Path,
) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    semantic = _adapted_summary(snapshot)
    patch = _summary_latex_patch().model_copy(deep=True)
    patch.replacements[0].latex = patch.replacements[0].latex.replace(
        "pour les systèmes énergétiques.",
        "pour les systèmes énergétiques. Experte Kubernetes certifiée.",
    )

    with pytest.raises(ValueError, match="adds content outside the semantic draft"):
        validate_latex_cv_patch(snapshot, patch, semantic.provenance, semantic.document)


def test_source_preserving_summary_rejects_residual_baseline_sentence(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    semantic = _adapted_summary(snapshot)
    patch = _summary_latex_patch().model_copy(deep=True)
    patch.replacements[0].latex += "Ancienne phrase du resume conservee par erreur.\n"

    with pytest.raises(ValueError, match="summary replacement is not exact"):
        validate_latex_cv_patch(snapshot, patch, semantic.provenance, semantic.document)


def test_technical_latex_repair_merges_one_block_without_touching_others() -> None:
    base = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="summary",
                source_ids=["summary.text"],
                latex="\\cvsection{Résumé}\nRésumé initial.\n",
            ),
            LatexBlockReplacement(
                block_id="skills",
                source_ids=["skills.section"],
                latex="\\cvsection{Compétences}\nData & AI\n",
            ),
        ]
    )
    repair = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="skills",
                source_ids=["skills.section"],
                latex="\\cvsection{Compétences}\nData \\& AI\n",
            )
        ]
    )

    merged = merge_latex_cv_patch(base, repair)

    assert merged.replacements[0] is base.replacements[0]
    assert merged.replacements[1].latex.endswith("Data \\& AI\n")


def test_renderer_rejects_latex_that_does_not_match_semantic_cv(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    semantic = _adapted_summary(snapshot)
    mismatched = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="summary",
                source_ids=["summary.text"],
                latex="\\cvsection{Résumé}\nTexte sans rapport avec l'adaptation validée.\n",
            )
        ]
    )
    draft = type(semantic)(
        document=semantic.document,
        provenance=semantic.provenance,
        latex_patch=mismatched,
    )

    with pytest.raises(ValueError, match="adds content outside the semantic draft"):
        DocumentRenderer().render_cv(snapshot, draft, tmp_path / "mismatch")


def test_semantic_guard_accepts_one_adapted_sentence_split_across_source_bullets(
    tmp_path: Path,
) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    semantic = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="experience.0.bullet.0",
                    value=(
                        "Développement de pipelines Python et SQL pour des données énergétiques, "
                        "avec contrôles qualité et supervision des traitements."
                    ),
                    fact_ids=["experience.gridlab.fact.1"],
                )
            ]
        ),
    )
    patch = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="experience",
                source_ids=["experience.0.bullet.0"],
                latex=(
                    "\\cvsection{Expérience}\n"
                    "\\textbf{GridLab -- Ingénieure données} \\hfill 2024--2026\n"
                    "\\begin{itemize}[leftmargin=*]\n"
                    "\\item Développement de pipelines Python et SQL pour des données énergétiques.\n"
                    "\\item Contrôles qualité et supervision des traitements.\n"
                    "\\end{itemize}\n"
                ),
            )
        ]
    )
    rendered_draft = type(semantic)(
        document=semantic.document,
        provenance=semantic.provenance,
        latex_patch=patch,
    )

    rendered = DocumentRenderer().render_cv(snapshot, rendered_draft, tmp_path / "split-bullets")

    assert rendered.page_count == 1


def test_noop_adaptation_keeps_import_and_only_adds_configured_layout(
    tmp_path: Path,
) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    semantic = apply_cv_patch(snapshot, CvAdaptationPatch())

    rendered = DocumentRenderer().render_cv(snapshot, semantic, tmp_path / "noop")

    mapping = snapshot.cv_mapping
    assert mapping is not None
    rendered_source = rendered.source_path.read_bytes()
    assert snapshot.cv_template_bytes == source
    assert rendered_source[: mapping.preamble_end_byte] == source[: mapping.preamble_end_byte]
    assert rendered_source.count(b"% JOBAUTO_LAYOUT") == 1
    assert rendered.page_count == 1


def test_identity_adaptation_rejects_foreign_contact_details(tmp_path: Path) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    semantic = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="headline.text",
                    value="Data Engineer | Python, SQL | Lyon",
                    fact_ids=["profile.summary"],
                )
            ]
        ),
    )
    mapping = snapshot.cv_mapping
    assert mapping is not None
    identity = next(block for block in mapping.blocks if block.block_id == "identity")
    identity_latex = source[identity.start_byte : identity.end_byte].decode("utf-8")
    identity_latex = identity_latex.replace(
        snapshot.cv_source.headline,
        "Data Engineer | Python, SQL | Lyon",
    ).replace(
        "camille.martin@example.test",
        "camille.martin@example.test | foreign@example.test",
    )
    assert "Data Engineer | Python, SQL | Lyon" in identity_latex
    patch = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="identity",
                source_ids=["headline.text"],
                latex=identity_latex,
            )
        ]
    )
    rendered_draft = type(semantic)(
        document=semantic.document,
        provenance=semantic.provenance,
        latex_patch=patch,
    )

    with pytest.raises(ValueError, match="identity details changed outside headline"):
        DocumentRenderer().render_cv(snapshot, rendered_draft, tmp_path / "foreign")


def test_locked_identity_allows_only_the_adapted_headline(tmp_path: Path) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    locked_blocks = [
        block.model_copy(update={"policy": SectionPolicy(fidelity=FidelityLevel.LOCKED)})
        if block.block_id == "identity"
        else block
        for block in mapping.blocks
    ]
    snapshot = replace(
        snapshot,
        _cv_mapping=LatexCvMapping.model_validate(
            {**mapping.model_dump(mode="json"), "blocks": locked_blocks}
        ),
    )
    semantic = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="headline.text",
                    value="Data Engineer | Python, SQL | Lyon",
                    fact_ids=["profile.summary"],
                )
            ]
        ),
    )
    identity = next(block for block in snapshot.cv_mapping.blocks if block.block_id == "identity")
    original = source[identity.start_byte : identity.end_byte].decode("utf-8")
    adapted = original.replace(
        snapshot.cv_source.headline,
        "Data Engineer | Python, SQL | Lyon",
    )
    patch = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="identity",
                source_ids=["headline.text"],
                latex=adapted,
            )
        ]
    )
    rendered = type(semantic)(
        document=semantic.document,
        provenance=semantic.provenance,
        latex_patch=patch,
    )

    result = DocumentRenderer().render_cv(snapshot, rendered, tmp_path / "headline")

    assert "Data Engineer" in result.extracted_text
    assert "camille.martin@example.test" in result.extracted_text
    assert "+33 1 00 00 00 01" in result.extracted_text


def test_locked_identity_rejects_contact_change_during_headline_adaptation(
    tmp_path: Path,
) -> None:
    source, snapshot = _source_snapshot(tmp_path)
    mapping = snapshot.cv_mapping
    assert mapping is not None
    locked_blocks = [
        block.model_copy(update={"policy": SectionPolicy(fidelity=FidelityLevel.LOCKED)})
        if block.block_id == "identity"
        else block
        for block in mapping.blocks
    ]
    snapshot = replace(
        snapshot,
        _cv_mapping=LatexCvMapping.model_validate(
            {**mapping.model_dump(mode="json"), "blocks": locked_blocks}
        ),
    )
    semantic = apply_cv_patch(
        snapshot,
        CvAdaptationPatch(
            changes=[
                CvFieldChange(
                    source_id="headline.text",
                    value="Data Engineer | Python, SQL | Lyon",
                    fact_ids=["profile.summary"],
                )
            ]
        ),
    )
    identity = next(block for block in snapshot.cv_mapping.blocks if block.block_id == "identity")
    adapted = source[identity.start_byte : identity.end_byte].decode("utf-8")
    adapted = adapted.replace(
        snapshot.cv_source.headline,
        "Data Engineer | Python, SQL | Lyon",
    ).replace("camille.martin@example.test", "foreign@example.test")
    patch = LatexCvPatch(
        replacements=[
            LatexBlockReplacement(
                block_id="identity",
                source_ids=["headline.text"],
                latex=adapted,
            )
        ]
    )
    rendered = type(semantic)(
        document=semantic.document,
        provenance=semantic.provenance,
        latex_patch=patch,
    )

    with pytest.raises(ValueError, match="identity details changed outside headline"):
        DocumentRenderer().render_cv(snapshot, rendered, tmp_path / "foreign-locked")


class _SourcePreservingLlm:
    def __init__(self) -> None:
        self.calls: list[tuple[type, GenerationPhase]] = []
        self.prompts: list[str] = []

    def complete_json(self, prompt, response_model, phase, **_kwargs):
        self.calls.append((response_model, phase))
        self.prompts.append(prompt)
        if response_model is CvAdaptationPatch:
            return CvAdaptationPatch(
                changes=[
                    CvFieldChange(
                        source_id="summary.text",
                        value=(
                            "Ingénieure data spécialisée dans les pipelines Python et SQL fiables "
                            "pour les systèmes énergétiques."
                        ),
                        fact_ids=["profile.summary"],
                    )
                ]
            )
        if response_model is LatexCvPatch:
            patch = _summary_latex_patch()
            patch.replacements.append(
                LatexBlockReplacement(
                    block_id="skills",
                    source_ids=["skills.section"],
                    latex=(
                        "\\cvsection{Compétences}\n"
                        "\\textbf{Data} : Python, BigQuery\\\\\n"
                        "\\textbf{Machine Learning} : Data quality\n"
                    ),
                )
            )
            return patch
        if response_model is CandidateLetterDraft:
            return CandidateLetterDraft(
                greeting="Madame, Monsieur,",
                paragraphs=[
                    "Je souhaite contribuer aux produits data de GridCo en mobilisant mon expérience des pipelines Python et SQL fiables."
                ],
                closing="Cordialement,\nCamille Martin",
                used_fact_ids=["profile.summary"],
            )
        raise AssertionError(f"Unexpected model: {response_model}")


class _DriftingLatexLlm(_SourcePreservingLlm):
    def __init__(self) -> None:
        super().__init__()
        self.latex_attempts = 0

    def complete_json(self, prompt, response_model, phase, **kwargs):
        if response_model is LatexCvPatch:
            self.latex_attempts += 1
            if self.latex_attempts == 1:
                self.calls.append((response_model, phase))
                self.prompts.append(prompt)
                return LatexCvPatch(
                    replacements=[
                        LatexBlockReplacement(
                            block_id="summary",
                            source_ids=["summary.text"],
                            latex=(
                                "\\cvsection{RÃ©sumÃ©}\n"
                                "IngÃ©nieure data avec une formulation diffÃ©rente.\n"
                            ),
                        )
                    ]
                )
        return super().complete_json(prompt, response_model, phase, **kwargs)


class _ConfusedSourceTargetLlm(_SourcePreservingLlm):
    def __init__(self, *, fail_on_cv_call: int) -> None:
        super().__init__()
        self.cv_calls = 0
        self.fail_on_cv_call = fail_on_cv_call

    def complete_json(self, prompt, response_model, phase, **kwargs):
        if response_model is CvAdaptationPatch:
            self.cv_calls += 1
            if self.cv_calls == self.fail_on_cv_call:
                self.calls.append((response_model, phase))
                self.prompts.append(prompt)
                return CvAdaptationPatch(
                    source_blocks=[
                        CvSourceBlockChange(
                            source_id="source_block.experience",
                            value="Updated experience wording.",
                            fact_ids=["source_block.experience"],
                        )
                    ]
                )
        return super().complete_json(prompt, response_model, phase, **kwargs)


def test_pipeline_repairs_evidence_id_used_as_a_cv_patch_target(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    llm = _ConfusedSourceTargetLlm(fail_on_cv_call=1)
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )

    package = pipeline.generate_candidate_documents(
        ApplicationRow(
            excel_row=1,
            company="GridCo",
            role="Data Engineer",
            url="https://example.test/jobs/data-engineer",
        ),
        "GridCo recherche une Data Engineer pour construire des pipelines Python et SQL fiables.",
        brief=_brief_with_snapshot_skill_shape(snapshot),
    )

    assert llm.cv_calls == 2
    assert package.cv_patch.source_blocks == []
    assert package.cv.document.summary.startswith("Ing")
    assert "separate namespaces" in llm.prompts[0]
    assert "allowed_source_blocks: []" in llm.prompts[1]
    assert "source_block.experience may support a claim" in llm.prompts[1]


def test_final_cv_repair_recovers_from_an_evidence_id_used_as_target(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    llm = _ConfusedSourceTargetLlm(fail_on_cv_call=2)
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    row = ApplicationRow(
        excel_row=1,
        company="GridCo",
        role="Data Engineer",
        url="https://example.test/jobs/data-engineer",
    )
    offer = (
        "GridCo recherche une Data Engineer pour construire des pipelines Python et SQL fiables."
    )
    package = pipeline.generate_candidate_documents(
        row,
        offer,
        brief=_brief_with_snapshot_skill_shape(snapshot),
    )
    review = CandidateApplicationReview(
        approved=False,
        score=78,
        ats_score=82,
        editorial_score=72,
        adaptation_score=76,
        blocking_issues=["The experience section duplicates one project."],
        warnings=[],
        letter_argument=_letter_argument(),
        requirement_coverage=[],
        repair_actions=[
            CandidateRepairAction(
                surface="cv",
                instruction="Remove the duplicated project while preserving the accepted evidence.",
            )
        ],
    )

    repaired = pipeline.repair_candidate_documents(row, package, review, offer)

    assert llm.cv_calls == 3
    assert repaired.cv_patch.source_blocks == []
    assert repaired.cv.document.summary.startswith("Ing")
    assert "CV PATCH CONTRACT VALIDATION FAILURE" in llm.prompts[-2]
    assert llm.calls[-2:] == [
        (CvAdaptationPatch, GenerationPhase.REPAIR),
        (LatexCvPatch, GenerationPhase.CV_LATEX_WRITER),
    ]


def test_pipeline_adds_latex_rendering_stage_only_for_source_preserving_profile(
    tmp_path: Path,
) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    llm = _SourcePreservingLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    row = ApplicationRow(
        excel_row=1,
        company="GridCo",
        role="Data Engineer",
        url="https://example.test/jobs/data-engineer",
    )

    package = pipeline.generate_candidate_documents(
        row,
        "GridCo recherche une Data Engineer pour construire des pipelines Python et SQL fiables.",
        brief=_brief_with_snapshot_skill_shape(snapshot),
    )
    rendered = DocumentRenderer().render_cv(snapshot, package.cv, tmp_path / "pipeline")

    assert package.cv.latex_patch is not None
    assert [phase for _model, phase in llm.calls] == [
        GenerationPhase.CV_WRITER,
        GenerationPhase.CV_LATEX_WRITER,
        GenerationPhase.LETTER_WRITER,
    ]
    assert "EDITABLE EXACT LATEX BLOCKS" in llm.prompts[1]
    assert "summary.text" in llm.prompts[1]
    assert "pipelines Python et SQL fiables" in rendered.extracted_text

    repaired = pipeline.repair_rendering_failure(
        row,
        package,
        surface="cv",
        error="cv PDF is missing adapted semantic content",
        offer_text="GridCo recherche une Data Engineer pour des pipelines fiables.",
    )
    assert repaired.cv.document == package.cv.document
    assert repaired.cv_patch == package.cv_patch
    assert repaired.letter == package.letter
    assert llm.calls[-1] == (LatexCvPatch, GenerationPhase.CV_LATEX_WRITER)
    assert "technical_only: true" in llm.prompts[-1]

    overflow_repaired = pipeline.repair_rendering_failure(
        row,
        package,
        surface="cv",
        error="cv cannot fit on one page within the configured readability bounds",
        offer_text="GridCo recherche une Data Engineer pour des pipelines fiables.",
    )
    assert overflow_repaired.cv.latex_patch is not None
    assert [phase for _model, phase in llm.calls[-2:]] == [
        GenerationPhase.REPAIR,
        GenerationPhase.CV_LATEX_WRITER,
    ]
    assert "no longer than the corresponding baseline CV" in llm.prompts[-2]
    assert "technical_only: true" not in llm.prompts[-1]


def test_pipeline_repairs_latex_wording_drift_before_rendering(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    llm = _DriftingLatexLlm()
    pipeline = CandidatePipeline.for_candidate(
        llm,
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )
    row = ApplicationRow(
        excel_row=1,
        company="GridCo",
        role="Data Engineer",
        url="https://example.test/jobs/data-engineer",
    )

    package = pipeline.generate_candidate_documents(
        row,
        "GridCo recherche une Data Engineer pour construire des pipelines Python et SQL fiables.",
        brief=_brief_with_snapshot_skill_shape(snapshot),
    )
    rendered = DocumentRenderer().render_cv(snapshot, package.cv, tmp_path / "repaired-drift")

    latex_prompts = [prompt for prompt in llm.prompts if "LATEX_CV_SPECIALIST" in prompt]
    assert llm.latex_attempts == 2
    assert "semantic_contract_error:" in latex_prompts[-1]
    assert "copies the ADAPTED STRUCTURED CV exactly" in latex_prompts[-1]
    assert "pipelines Python et SQL fiables" in rendered.extracted_text


def test_strategy_validation_respects_candidate_project_creation_policy(
    tmp_path: Path,
) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    pipeline = CandidatePipeline.for_candidate(
        _SourcePreservingLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )

    with pytest.raises(ValueError, match="forbids creating"):
        pipeline._validate_lean_brief_fact_ids(_brief())


def test_strategy_skill_budget_is_derived_from_the_imported_cv_shape(tmp_path: Path) -> None:
    _source, snapshot = _source_snapshot(tmp_path)
    snapshot = replace(
        snapshot,
        _profile=snapshot.profile.model_copy(
            update={
                "project_lab": snapshot.profile.project_lab.model_copy(
                    update={
                        "minimum_visible_projects": 0,
                        "maximum_visible_projects": 0,
                    }
                )
            }
        ),
    )
    budget = source_skill_line_budget(snapshot)
    assert budget is not None
    brief = _brief_with_snapshot_skill_shape(snapshot).model_copy(
        update={
            "project_plan": ProjectPlan(
                decision="none",
                rationale="This isolated skill-layout test does not exercise project selection.",
            )
        }
    )
    category = brief.skill_plan.categories[0]
    template_item = brief.skill_plan.items[0]
    overflowing = SkillPlan(
        categories=[category],
        items=[
            template_item.model_copy(update={"name": "A" * 60}),
            template_item.model_copy(update={"name": "B" * 60}),
        ],
        rationale="Exercise the source-derived competency row budget.",
    )
    pipeline = CandidatePipeline.for_candidate(
        _SourcePreservingLlm(),
        snapshot,
        CandidateContext.from_snapshot(snapshot),
    )

    with pytest.raises(ValueError, match="cv_skill_presentation_budget"):
        pipeline._validate_lean_brief_fact_ids(brief.model_copy(update={"skill_plan": overflowing}))
