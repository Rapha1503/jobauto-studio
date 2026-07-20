from pathlib import Path

from jobauto.run_store import RunRecord, RunStore


def test_run_store_persists_atomic_phase_transitions(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    record = RunRecord(
        run_id="alex-run-12345678",
        candidate_id="alex-morgan",
        profile_path=tmp_path / "profile.yaml",
        status="pending",
        current_phase="pending",
        phase_history=["pending"],
        created_at="2026-07-16T10:00:00+00:00",
        updated_at="2026-07-16T10:00:00+00:00",
        offer_sha256="a" * 64,
        snapshot_hash="b" * 64,
        context_hash="c" * 64,
        run_dir=tmp_path / "runs" / "alex-morgan" / "alex-run-12345678",
    )

    store.create(record)
    updated = store.transition(record, status="running", phase="generating_documents")

    assert store.get(record.run_id) == updated
    assert updated.phase_history == ["pending", "generating_documents"]
    assert not list(updated.run_dir.glob(".run.json.*.tmp"))
    assert store.list_for_candidate("alex-morgan") == [updated]
    assert store.list_for_candidate("another-candidate") == []
