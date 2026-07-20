from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "plugins" / "jobauto" / "skills" / "jobauto-apply" / "scripts" / "jobauto_queue.py"
SKILL = ROOT / "plugins" / "jobauto" / "skills" / "jobauto-apply" / "SKILL.md"


class _StudioHandler(BaseHTTPRequestHandler):
    receipt: dict[str, object] | None = None
    artifacts: dict[str, tuple[str, str]] = {}

    @classmethod
    def _packet(cls) -> dict[str, object]:
        return {
            "handoff_id": "handoff-1",
            "campaign_id": "campaign-1",
            "candidate_id": "candidate-1",
            "company": "Example Health",
            "role": "Regulatory Affairs Specialist",
            "offer_url": "https://careers.example.test/jobs/1",
            "status": "claimed_for_chrome",
            "artifacts": [
                {
                    "kind": "cv",
                    "path": cls.artifacts["cv"][0],
                    "sha256": cls.artifacts["cv"][1],
                },
                {
                    "kind": "letter",
                    "path": cls.artifacts["letter"][0],
                    "sha256": cls.artifacts["letter"][1],
                },
            ],
            "preferences": {"mode": "automatic"},
            "blockers": [],
        }

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/campaigns/campaign-1/submission/claim-next":
            self._json(
                {
                    "queue": {
                        "campaign_id": "campaign-1",
                        "candidate_id": "candidate-1",
                        "mode": "automatic",
                        "status": "in_progress",
                        "ready_count": 0,
                        "claimed_count": 1,
                        "submitted_count": 0,
                        "blocked_count": 0,
                        "waiting_count": 0,
                    },
                    "packet": self._packet(),
                }
            )
            return
        if self.path == "/handoffs/handoff-1/receipt":
            type(self).receipt = body
            self._json({"handoff_id": "handoff-1", **body})
            return
        if self.path == "/handoffs/handoff-1/release":
            self._json({**self._packet(), "status": "ready_for_chrome"})
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture
def studio_url(tmp_path: Path):
    _StudioHandler.receipt = None
    artifact_paths = {}
    for kind in ("cv", "letter"):
        path = tmp_path / f"{kind}.pdf"
        path.write_bytes(f"approved {kind}".encode())
        artifact_paths[kind] = (str(path), hashlib.sha256(path.read_bytes()).hexdigest())
    _StudioHandler.artifacts = artifact_paths
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StudioHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_plugin_client_fetches_packet_and_records_receipt(studio_url: str) -> None:
    fetched = _run("next", "--base-url", studio_url, "--campaign-id", "campaign-1")

    assert fetched.returncode == 0, fetched.stderr
    packet = json.loads(fetched.stdout)
    assert packet["queue"]["claimed_count"] == 1
    assert packet["packet"]["handoff_id"] == "handoff-1"
    assert packet["packet"]["role"] == "Regulatory Affairs Specialist"

    recorded = _run(
        "receipt",
        "--base-url",
        studio_url,
        "--handoff-id",
        "handoff-1",
        "--status",
        "submitted",
        "--portal",
        "example-careers",
        "--confirmation-url",
        "https://careers.example.test/confirmation/1",
        "--filled-field",
        "email",
        "--uploaded-file",
        "cv.pdf",
    )

    assert recorded.returncode == 0, recorded.stderr
    assert _StudioHandler.receipt == {
        "status": "submitted",
        "portal": "example-careers",
        "confirmation_url": "https://careers.example.test/confirmation/1",
        "evidence_path": None,
        "filled_fields": ["email"],
        "uploaded_files": ["cv.pdf"],
        "blockers": [],
        "warnings": [],
    }


def test_plugin_client_rejects_remote_servers() -> None:
    result = _run(
        "next",
        "--base-url",
        "https://example.com",
        "--campaign-id",
        "campaign-1",
    )

    assert result.returncode == 1
    assert "only connects to localhost" in result.stderr


def test_plugin_client_rejects_a_modified_approved_file(studio_url: str) -> None:
    Path(_StudioHandler.artifacts["cv"][0]).write_bytes(b"modified after approval")

    result = _run("next", "--base-url", studio_url, "--campaign-id", "campaign-1")

    assert result.returncode == 1
    assert "approved cv file no longer matches its hash" in result.stderr


def test_plugin_client_can_release_an_unused_claim(studio_url: str) -> None:
    result = _run(
        "release",
        "--base-url",
        studio_url,
        "--handoff-id",
        "handoff-1",
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "ready_for_chrome"


def test_skill_requires_user_chrome_and_visible_confirmation() -> None:
    content = " ".join(SKILL.read_text(encoding="utf-8").split())

    assert "Codex Chrome extension" in content
    assert "Allow access to file URLs" in content
    assert "selected file remains empty" in content
    assert "Never run two live submissions in parallel" in content
    assert "visible employer confirmation" in content
