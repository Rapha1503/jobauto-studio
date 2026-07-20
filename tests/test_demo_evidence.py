from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).parents[1]
EVIDENCE = ROOT / "docs" / "demo-evidence" / "20260718-nonit-chrome-batch" / "artifacts"


def test_canonical_batch_evidence_contains_five_verifiable_packages() -> None:
    manifest = json.loads((EVIDENCE / "manifest.json").read_text(encoding="utf-8-sig"))

    assert len(manifest) == 5
    for package in manifest:
        assert package["status"] == "completed"
        assert package["final_review"]["approved"] is True
        assert package["final_review"]["score"] >= 90

        for kind in ("cv", "letter"):
            artifact = EVIDENCE / package[kind]["file"]
            assert artifact.is_file()
            assert hashlib.sha256(artifact.read_bytes()).hexdigest() == package[kind]["sha256"]
            assert len(PdfReader(artifact).pages) == package[kind]["pages"] == 1

        trace = EVIDENCE / package["agent_trace"]["file"]
        events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        phases = {event["phase"] for event in events}
        assert {"offer_analysis", "cv_writer", "letter_writer", "final_review"} <= phases
        assert all(event["codex_model"] == package["model"] for event in events)
