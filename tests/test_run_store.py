from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jobauto.run_store as run_store_module
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


def test_run_store_retries_transient_windows_replace_lock(tmp_path: Path, monkeypatch) -> None:
    real_replace = run_store_module.os.replace
    attempts = 0

    def transiently_locked_replace(source: Path, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError(5, "Access is denied", str(target))
        real_replace(source, target)

    monkeypatch.setattr(run_store_module.os, "replace", transiently_locked_replace)
    monkeypatch.setattr(run_store_module.time, "sleep", lambda _delay: None)

    store = RunStore(tmp_path / "runs")
    record = RunRecord(
        run_id="alex-run-87654321",
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
        run_dir=tmp_path / "runs" / "alex-morgan" / "alex-run-87654321",
    )

    store.create(record)

    assert attempts == 3
    assert store.get(record.run_id) == record
    assert not list(record.run_dir.glob(".run.json.*.tmp"))


def test_run_store_serializes_status_reads_and_writes(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    record = RunRecord(
        run_id="alex-run-concurrent",
        candidate_id="alex-morgan",
        profile_path=tmp_path / "profile.yaml",
        status="running",
        current_phase="generating_documents",
        phase_history=["pending", "generating_documents"],
        created_at="2026-07-16T10:00:00+00:00",
        updated_at="2026-07-16T10:00:00+00:00",
        offer_sha256="a" * 64,
        snapshot_hash="b" * 64,
        context_hash="c" * 64,
        run_dir=tmp_path / "runs" / "alex-morgan" / "alex-run-concurrent",
    )
    store.create(record)

    def read_status() -> None:
        for _ in range(250):
            assert store.get(record.run_id).run_id == record.run_id

    def write_status(worker: int) -> None:
        for index in range(100):
            store.save(
                record.model_copy(
                    update={
                        "current_phase": f"worker-{worker}-{index}",
                        "updated_at": f"2026-07-16T10:00:{index % 60:02d}+00:00",
                    }
                )
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(read_status) for _ in range(4)]
        futures.extend(executor.submit(write_status, worker) for worker in range(2))
        for future in futures:
            future.result()

    assert store.get(record.run_id).run_id == record.run_id
    assert not list(record.run_dir.glob(".run.json.*.tmp"))
