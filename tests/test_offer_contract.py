from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from test_candidate_writers import _brief, _snapshot

from jobauto.adaptation_policy import FidelityLevel
from jobauto.ats import normalize_baseline_assessment
from jobauto.candidate_context import CandidateContext
from jobauto.candidate_pipeline import CandidatePipeline
from jobauto.facts import FactStore
from jobauto.models import (
    ApplicationBrief,
    ApplicationRow,
    BaselineCvCoverage,
    CandidateEvidenceAssessment,
    CandidateFact,
    EvidenceMapping,
    FactStatus,
    OfferContract,
    OfferRequirement,
    RenderedRequirementCoverage,
)
from jobauto.offer_contract import (
    OfferContractStore,
    candidate_evidence_key,
    lock_application_brief_to_offer_contract,
    offer_contract_key,
    validate_offer_contract,
)


def _contract(*, requirement: str = "Build reliable Python services") -> OfferContract:
    return OfferContract(
        company="Example",
        role="Applied Engineer",
        normalized_role="Applied Engineer",
        role_family="Engineering",
        language="en",
        open_role="Applied Engineer",
        sector="Software",
        specialisations=["Applied systems"],
        summary="Build and evaluate reliable applied systems for business users.",
        responsibilities=["Build reliable Python services"],
        required_skills=["Python"],
        preferred_skills=["SQL"],
        company_details=["Product team"],
        seniority="unspecified",
        targeted_keywords=["Python", "reliability"],
        requirements=[
            OfferRequirement(
                requirement_id="req_01",
                requirement=requirement,
                source_excerpt="Build reliable Python services",
                priority="must",
                matching_mode="exact_term",
                ats_terms=["Python"],
                kind="technical_skill",
            )
        ],
    )


def test_offer_contract_cache_is_candidate_and_preset_independent(tmp_path: Path) -> None:
    row = ApplicationRow(
        excel_row=1,
        company="Example",
        role="Applied Engineer",
        url="https://example.test/jobs/1",
    )
    offer = "Example needs an Applied Engineer to Build reliable Python services."
    store = OfferContractStore(tmp_path)
    key = offer_contract_key(row, offer)

    store.save(key, _contract())

    assert store.load(key) == _contract()
    assert offer_contract_key(row, offer) == key


def test_offer_contract_cache_parallel_writes_stay_valid(tmp_path: Path) -> None:
    store = OfferContractStore(tmp_path)
    key = "shared-contract"
    contract = _contract()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _index: store.save(key, contract), range(32)))

    assert store.load(key) == contract
    assert list(tmp_path.glob("*.tmp")) == []


def test_offer_contract_rejects_requirement_excerpt_absent_from_offer() -> None:
    with pytest.raises(ValueError, match="source_excerpt"):
        validate_offer_contract(_contract(), "A different and unrelated offer description.")


def test_offer_requirement_deduplicates_case_only_ats_terms() -> None:
    requirement = (
        _contract()
        .requirements[0]
        .model_copy(update={"ats_terms": ["SQL", "Sql", "sql", "Python"]})
    )

    normalized = OfferRequirement.model_validate(requirement.model_dump())

    assert normalized.ats_terms == ["SQL", "Python"]


def test_semantic_requirement_drops_agent_generated_ats_paraphrases() -> None:
    requirement = (
        _contract()
        .requirements[0]
        .model_copy(
            update={
                "matching_mode": "semantic_concept",
                "ats_terms": ["service industrialization"],
            }
        )
    )

    normalized = OfferRequirement.model_validate(requirement.model_dump())

    assert normalized.ats_terms == []


def test_offer_contract_rejects_paraphrased_ats_terms() -> None:
    contract = _contract().model_copy(
        update={
            "requirements": [
                _contract()
                .requirements[0]
                .model_copy(update={"ats_terms": ["service industrialization"]})
            ]
        }
    )
    offer = "Example needs an engineer to Build reliable Python services."

    with pytest.raises(ValueError, match="ats_terms=req_01:service industrialization"):
        validate_offer_contract(contract, offer)


def test_offer_contract_is_repaired_before_candidate_strategy(tmp_path: Path) -> None:
    invalid = _contract().model_copy(
        update={
            "requirements": [
                _contract()
                .requirements[0]
                .model_copy(update={"ats_terms": ["service industrialization"]})
            ]
        }
    )

    class RepairingContractLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.telemetry_log: list[dict[str, object]] = []

        def complete_json(self, prompt, schema, phase):
            self.prompts.append(prompt)
            self.telemetry_log.append({"phase": phase.value, "status": "succeeded"})
            assert schema is OfferContract
            return invalid if len(self.prompts) == 1 else _contract()

    llm = RepairingContractLlm()
    pipeline = CandidatePipeline(
        llm,
        FactStore(
            [
                CandidateFact(
                    fact_id="candidate.fact",
                    claim="Candidate-owned evidence",
                    status=FactStatus.VERIFIED,
                )
            ]
        ),
        offer_contract_store=OfferContractStore(tmp_path),
    )
    row = ApplicationRow(
        excel_row=1,
        company="Example",
        role="Applied Engineer",
        url="https://example.test/jobs/1",
    )
    offer = "Example needs an engineer to Build reliable Python services."

    contract = pipeline._load_or_generate_offer_contract(row, offer)

    assert contract == _contract()
    assert len(llm.prompts) == 2
    assert "literal contiguous substring" in llm.prompts[1]
    assert llm.telemetry_log[0]["rejection_category"] == "offer_contract_source_validation"


def test_application_brief_cannot_mutate_canonical_offer_requirements() -> None:
    brief: ApplicationBrief = _brief().model_copy(
        update={
            "company": "Wrong company",
            "role": "Wrong role",
            "requirements": [
                OfferRequirement(
                    requirement_id="changed",
                    requirement="Candidate-dependent requirement",
                    source_excerpt="Candidate-dependent requirement",
                    priority="nice",
                    kind="other",
                )
            ],
        }
    )

    locked = lock_application_brief_to_offer_contract(brief, _contract())

    assert locked.company == "Example"
    assert locked.role == "Applied Engineer"
    assert locked.requirements == _contract().requirements


def test_candidate_pipelines_reuse_one_candidate_independent_offer_contract(
    tmp_path: Path,
) -> None:
    class ContractLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_json(self, prompt, schema, phase):
            self.prompts.append(prompt)
            assert schema is OfferContract
            assert phase.value == "offer_analysis"
            return _contract()

    class NoCallLlm:
        def complete_json(self, *_args, **_kwargs):
            raise AssertionError("cached offer contract should avoid another agent call")

    facts = FactStore(
        [
            CandidateFact(
                fact_id="candidate.fact",
                claim="Candidate-owned evidence",
                status=FactStatus.VERIFIED,
            )
        ]
    )
    store = OfferContractStore(tmp_path)
    row = ApplicationRow(
        excel_row=1,
        company="Example",
        role="Applied Engineer",
        url="https://example.test/jobs/1",
    )
    offer = "Example needs an Applied Engineer to Build reliable Python services."
    llm = ContractLlm()
    first = CandidatePipeline(llm, facts, offer_contract_store=store)
    second = CandidatePipeline(NoCallLlm(), facts, offer_contract_store=store)

    first_contract = first._load_or_generate_offer_contract(row, offer)
    second_contract = second._load_or_generate_offer_contract(row, offer)

    assert first_contract == second_contract == _contract()
    assert len(llm.prompts) == 1
    assert "CandidateContext" not in llm.prompts[0]
    assert "adaptation mode" in llm.prompts[0]


def _baseline_coverage() -> BaselineCvCoverage:
    return BaselineCvCoverage(
        role_positioning_matches=True,
        language_matches=True,
        requirement_coverage=[
            RenderedRequirementCoverage(
                requirement_id="req_01",
                coverage="exact",
                placements=["Headline", "Skills"],
                supporting_excerpts=["Python"],
                rationale="Python is literally visible in the baseline CV.",
            )
        ],
    )


def test_baseline_cv_audit_is_cached_independently_of_adaptation_strategy(
    tmp_path: Path,
) -> None:
    class CoverageLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_json(self, prompt, schema, phase):
            self.prompts.append(prompt)
            assert schema is BaselineCvCoverage
            assert phase.value == "baseline_ats"
            return _baseline_coverage()

    class NoCallLlm:
        def complete_json(self, *_args, **_kwargs):
            raise AssertionError("cached baseline audit should avoid another agent call")

    snapshot = _snapshot()
    store = OfferContractStore(tmp_path)
    llm = CoverageLlm()
    facts = FactStore(
        [
            CandidateFact(
                fact_id="candidate.fact",
                claim="Candidate-owned evidence",
                status=FactStatus.VERIFIED,
            )
        ]
    )
    first = CandidatePipeline(
        llm,
        facts,
        candidate_snapshot=snapshot,
        offer_contract_store=store,
    )
    second = CandidatePipeline(
        NoCallLlm(),
        facts,
        candidate_snapshot=snapshot,
        offer_contract_store=store,
    )
    offer = "Example needs an Applied Engineer to Build reliable Python services."

    first_coverage = first._load_or_generate_baseline_coverage(_contract(), offer)
    second_coverage = second._load_or_generate_baseline_coverage(_contract(), offer)

    assert first_coverage == second_coverage == _baseline_coverage()
    assert len(llm.prompts) == 1
    assert "CandidateContext" not in llm.prompts[0]
    assert "FidelityLevel" not in llm.prompts[0]


def _candidate_evidence(fact_id: str) -> CandidateEvidenceAssessment:
    return CandidateEvidenceAssessment(
        evidence_mappings=[
            EvidenceMapping(
                requirement_id="req_01",
                evidence_level="verified",
                fact_ids=[fact_id],
                rationale="The approved candidate evidence directly establishes Python use.",
            )
        ]
    )


def test_candidate_evidence_cache_is_independent_of_adaptation_preset(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    sections = dict(snapshot.adaptation_policy.documents["cv"].sections)
    sections["summary"] = sections["summary"].model_copy(
        update={"fidelity": FidelityLevel.HIGHLY_ADAPTABLE}
    )
    flexible_policy = snapshot.adaptation_policy.model_copy(
        update={
            "policy_id": "flexible-test",
            "documents": {
                **snapshot.adaptation_policy.documents,
                "cv": snapshot.adaptation_policy.documents["cv"].model_copy(
                    update={"sections": sections}
                ),
            },
        }
    )
    flexible_snapshot = replace(snapshot, _adaptation_policy=flexible_policy)
    balanced_context = CandidateContext.from_snapshot(snapshot)
    flexible_context = CandidateContext.from_snapshot(flexible_snapshot)
    assert balanced_context.context_hash != flexible_context.context_hash
    assert candidate_evidence_key(_contract(), balanced_context) == candidate_evidence_key(
        _contract(), flexible_context
    )

    fact_id = sorted(snapshot.evidence_ids)[0]
    expected = _candidate_evidence(fact_id)

    class EvidenceLlm:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def complete_json(self, prompt, schema, phase):
            self.prompts.append(prompt)
            assert schema is CandidateEvidenceAssessment
            assert phase.value == "candidate_evidence"
            return expected

    class NoCallLlm:
        def complete_json(self, *_args, **_kwargs):
            raise AssertionError("cached candidate evidence should avoid another agent call")

    store = OfferContractStore(tmp_path)
    first_llm = EvidenceLlm()
    first = CandidatePipeline(
        first_llm,
        snapshot.facts,
        candidate_snapshot=snapshot,
        candidate_context=balanced_context,
        offer_contract_store=store,
    )
    second = CandidatePipeline(
        NoCallLlm(),
        flexible_snapshot.facts,
        candidate_snapshot=flexible_snapshot,
        candidate_context=flexible_context,
        offer_contract_store=store,
    )

    assert first._load_or_generate_candidate_evidence(_contract()) == expected
    assert second._load_or_generate_candidate_evidence(_contract()) == expected
    assert len(first_llm.prompts) == 1
    assert "adaptation_policy" not in first_llm.prompts[0]


def test_balanced_and_flexible_strategy_cannot_change_the_baseline_ats_score() -> None:
    contract = _contract()
    baseline_cv = "Applied Engineer with Python experience."
    first = _brief().model_copy(
        update={
            "requirements": contract.requirements,
            "baseline_cv_assessment": _brief().baseline_cv_assessment.model_copy(
                update={
                    "requirement_coverage": [
                        RenderedRequirementCoverage(
                            requirement_id="req_01",
                            coverage="missing",
                            placements=[],
                            rationale="A deliberately divergent strategy audit.",
                        )
                    ],
                    "improvable_requirement_ids": ["req_01"],
                }
            ),
        }
    )
    second = _brief().model_copy(
        update={
            "requirements": contract.requirements,
            "baseline_cv_assessment": _brief().baseline_cv_assessment.model_copy(
                update={
                    "requirement_coverage": [
                        RenderedRequirementCoverage(
                            requirement_id="req_01",
                            coverage="indirect",
                            placements=["Summary"],
                            supporting_excerpts=["Python"],
                            rationale="Another deliberately divergent strategy audit.",
                        )
                    ],
                    "improvable_requirement_ids": [],
                }
            ),
        }
    )

    locked_first = CandidatePipeline._lock_baseline_coverage(first, _baseline_coverage())
    locked_second = CandidatePipeline._lock_baseline_coverage(second, _baseline_coverage())
    normalized_first = normalize_baseline_assessment(locked_first, baseline_cv)
    normalized_second = normalize_baseline_assessment(locked_second, baseline_cv)

    assert normalized_first.baseline_cv_assessment.ats_score == 100
    assert normalized_second.baseline_cv_assessment.ats_score == 100
    assert (
        normalized_first.baseline_cv_assessment.requirement_coverage
        == normalized_second.baseline_cv_assessment.requirement_coverage
    )


def test_candidate_evidence_and_visible_gaps_are_stable_across_presets() -> None:
    contract = _contract()
    baseline_cv = "Applied Engineer experience."
    coverage = BaselineCvCoverage(
        role_positioning_matches=True,
        language_matches=True,
        requirement_coverage=[
            RenderedRequirementCoverage(
                requirement_id="req_01",
                coverage="missing",
                placements=[],
                rationale="Python is not visible in the baseline CV.",
            )
        ],
    )
    evidence = _candidate_evidence("candidate.fact")
    assessments = []
    for gap, improvable in (
        (["Preset-specific wording A"], []),
        (["Preset-specific wording B"], ["req_01"]),
    ):
        brief = _brief().model_copy(
            update={
                "requirements": contract.requirements,
                "baseline_cv_assessment": _brief().baseline_cv_assessment.model_copy(
                    update={
                        "material_gaps": gap,
                        "improvable_requirement_ids": improvable,
                        "requirement_coverage": coverage.requirement_coverage,
                    }
                ),
            }
        )
        brief = CandidatePipeline._lock_baseline_coverage(brief, coverage)
        brief = CandidatePipeline._lock_candidate_evidence(brief, evidence)
        assessments.append(normalize_baseline_assessment(brief, baseline_cv))

    first = assessments[0].baseline_cv_assessment
    second = assessments[1].baseline_cv_assessment
    assert first.ats_score == second.ats_score == 0
    assert first.improvable_requirement_ids == second.improvable_requirement_ids == ["req_01"]
    assert first.material_gaps == second.material_gaps == ["req_01: Build reliable Python services"]
