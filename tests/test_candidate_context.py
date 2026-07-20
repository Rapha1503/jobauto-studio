import hashlib
import json
from pathlib import Path

import pytest

import jobauto.candidate_context as candidate_context_module
from jobauto.candidate_context import CandidateContext, ContextPurpose
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.latex_cv_source import TexBlockKind, analyze_latex_cv


def _snapshot() -> object:
    project_root = Path(__file__).resolve().parents[1]
    return CandidateProfileRepository(project_root / "config" / "profiles").load_snapshot(
        project_root / "config" / "profiles" / "example" / "profile.yaml"
    )


def test_context_serializes_complete_candidate_snapshot_deterministically() -> None:
    snapshot = _snapshot()

    first = CandidateContext.from_snapshot(snapshot)
    second = CandidateContext.from_snapshot(snapshot)

    payload = first.payload
    assert first.context_hash == second.context_hash
    assert first.serialized == second.serialized
    assert first.context_hash == hashlib.sha256(first.serialized.encode("utf-8")).hexdigest()
    assert payload["candidate_id"] == "alex-morgan"
    assert payload["locale"] == "en-GB"
    assert payload["identity"] == {
        "email": "alex.morgan@example.test",
        "first_name": "Alex",
        "last_name": "Morgan",
        "location": "Toulouse",
        "phone": "+33 1 00 00 00 00",
    }
    assert payload["baseline_cv"] == snapshot.cv_source.model_dump(mode="json")
    assert payload["letter_reference"] == snapshot.letter_reference
    assert payload["facts"] == [
        fact.model_dump(mode="json")
        for fact in sorted(snapshot.facts.facts, key=lambda fact: fact.fact_id)
    ]
    assert payload["unapproved_fact_ids"] == []
    assert payload["evidence_ids"] == sorted(snapshot.evidence_ids)
    assert payload["projects"] == [
        entry.model_dump(mode="json")
        for entry in sorted(snapshot.project_bank.entries, key=lambda entry: entry.id)
    ]
    assert payload["skill_policy"] == snapshot.skill_policy.context_data()
    assert payload["adaptation_policy"] == snapshot.adaptation_policy.model_dump(mode="json")
    assert payload["search_preferences"] == snapshot.search_preferences.model_dump(mode="json")
    assert payload["asset_hashes"] == dict(snapshot.asset_hashes)
    assert payload["snapshot_hash"] == snapshot.snapshot_hash


def test_example_context_contains_no_foreign_private_sentinels() -> None:
    snapshot = _snapshot()
    context = CandidateContext.from_snapshot(snapshot)

    serialized = context.serialized.casefold()
    assert "privatecandidate" not in serialized
    assert "privateemployer" not in serialized
    assert "privateschool" not in serialized
    assert "c:\\users\\" not in serialized
    for path in snapshot.profile.asset_paths.values():
        assert str(path).casefold() not in serialized


def test_context_payload_is_an_independent_copy() -> None:
    context = CandidateContext.from_snapshot(_snapshot())
    exposed = context.payload

    exposed["identity"]["first_name"] = "Mutated"

    assert context.payload["identity"]["first_name"] == "Alex"


def test_prompt_views_keep_only_phase_relevant_candidate_data() -> None:
    context = CandidateContext.from_snapshot(_snapshot())

    strategy = context.prompt_view(ContextPurpose.STRATEGY)
    cv = context.prompt_view(ContextPurpose.CV_WRITER)
    letter = context.prompt_view(ContextPurpose.LETTER_WRITER)
    review = context.prompt_view(ContextPurpose.SUPERVISOR)

    assert strategy.parent_context_hash == context.context_hash
    assert strategy.payload["candidate_id"] == "alex-morgan"
    assert "facts" in strategy.payload
    assert "projects" in strategy.payload
    assert "skill_policy" in strategy.payload
    assert strategy.payload["baseline_cv"] == context.payload["baseline_cv"]
    assert "identity" not in strategy.payload
    assert "source_preserving_blocks" not in strategy.payload
    assert "letter_reference" not in strategy.payload
    assert "search_preferences" not in strategy.payload
    assert "asset_hashes" not in strategy.payload

    assert "baseline_cv" in cv.payload
    assert "source_preserving_blocks" not in cv.payload
    assert "identity" in cv.payload
    assert "letter_reference" not in cv.payload
    assert "search_preferences" not in cv.payload

    assert "letter_reference" in letter.payload
    assert "facts" in letter.payload
    assert "identity" in letter.payload
    assert "baseline_cv" not in letter.payload
    assert "source_preserving_blocks" not in letter.payload

    assert "source_preserving_blocks" in review.payload
    assert "baseline_cv" in review.payload
    assert "facts" in review.payload
    assert "letter_reference" not in review.payload
    assert "search_preferences" not in review.payload


def test_prompt_views_are_deterministic_and_smaller_than_full_context() -> None:
    context = CandidateContext.from_snapshot(_snapshot())

    first = context.prompt_view(ContextPurpose.LETTER_WRITER)
    second = context.prompt_view(ContextPurpose.LETTER_WRITER)

    assert first == second
    assert first.view_hash == hashlib.sha256(first.serialized.encode("utf-8")).hexdigest()
    assert len(first.serialized) < len(context.serialized)


def test_write_context_capsule_writes_hash_and_exact_logical_asset_allowlist(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    context = CandidateContext.from_snapshot(snapshot)

    capsule = context.write_context_capsule(tmp_path / "run-context")

    context_path = capsule.root / "candidate_context.json"
    manifest = json.loads(capsule.manifest_path.read_text(encoding="utf-8"))
    assert json.loads(context_path.read_text(encoding="utf-8")) == context.payload
    assert capsule.context_hash == context.context_hash
    assert capsule.asset_allowlist == tuple(sorted(snapshot.asset_hashes))
    assert manifest == {
        "asset_allowlist": list(capsule.asset_allowlist),
        "context_hash": context.context_hash,
    }
    assert (
        str(Path.cwd().resolve()).casefold()
        not in context_path.read_text(encoding="utf-8").casefold()
    )


def test_context_capsule_is_not_published_partially(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = CandidateContext.from_snapshot(_snapshot())
    target = tmp_path / "run-context"
    real_atomic_write = candidate_context_module._atomic_write
    calls = 0

    def fail_second_write(path: Path, content: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated manifest failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(candidate_context_module, "_atomic_write", fail_second_write)

    with pytest.raises(OSError, match="simulated manifest failure"):
        context.write_context_capsule(target)

    assert not target.exists()


def test_non_identity_tex_sections_become_prompt_visible_evidence() -> None:
    source = rb"""\documentclass{article}
\newcommand{\cvsection}[1]{\section*{#1}}
\begin{document}
Alex Morgan
\cvsection{Languages}
French native | English C1
\cvsection{Certifications}
ISO 13485 Lead Auditor
\end{document}
"""
    mapping = analyze_latex_cv(source, filename="specialist.tex")
    snapshot = type("Snapshot", (), {"cv_mapping": mapping, "cv_template_bytes": source})()

    blocks = candidate_context_module._additional_evidence_blocks(snapshot)

    assert [block["evidence_id"] for block in blocks] == [
        "source_block.languages",
        "source_block.other",
    ]
    assert blocks[0]["label"] == "Languages"
    assert "English C1" in blocks[0]["latex"]
    assert blocks[1]["label"] == "Certifications"
    assert "ISO 13485 Lead Auditor" in blocks[1]["latex"]
    assert next(block for block in mapping.blocks if block.kind is TexBlockKind.OTHER)
