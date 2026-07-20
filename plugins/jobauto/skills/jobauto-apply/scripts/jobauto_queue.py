from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _local_base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.rstrip("/"))
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base URL must use http or https")
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("JobAuto queue client only connects to localhost")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a path, query or fragment")
    return value.rstrip("/")


def _request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{_local_base_url(base_url)}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"JobAuto Studio returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach JobAuto Studio: {exc.reason}") from exc


def next_packet(base_url: str, campaign_id: str) -> dict[str, Any]:
    response = _request_json(
        base_url,
        f"/campaigns/{urllib.parse.quote(campaign_id, safe='')}/submission/claim-next",
        method="POST",
        payload={},
    )
    queue = response.get("queue", {})
    packet = response.get("packet")
    if packet is not None:
        if packet.get("status") != "claimed_for_chrome":
            raise ValueError("Studio returned an unclaimed Chrome packet")
        _verify_packet_artifacts(packet)
    summary = {
        key: queue.get(key)
        for key in (
            "campaign_id",
            "candidate_id",
            "mode",
            "status",
            "ready_count",
            "claimed_count",
            "submitted_count",
            "blocked_count",
            "waiting_count",
        )
    }
    return {"queue": summary, "packet": packet}


def release_claim(base_url: str, handoff_id: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(handoff_id, safe="")
    return _request_json(
        base_url,
        f"/handoffs/{quoted}/release",
        method="POST",
        payload={},
    )


def _verify_packet_artifacts(packet: dict[str, Any]) -> None:
    if packet.get("blockers"):
        raise ValueError(f"handoff has blockers: {packet['blockers']}")
    artifacts = packet.get("artifacts", [])
    by_kind = {str(item.get("kind")): item for item in artifacts}
    missing = sorted({"cv", "letter"} - set(by_kind))
    if missing:
        raise ValueError(f"handoff is missing approved artifacts: {missing}")
    for kind in ("cv", "letter"):
        artifact = by_kind[kind]
        path = Path(str(artifact.get("path", ""))).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"approved {kind} file does not exist: {path}")
        expected = str(artifact.get("sha256", ""))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"approved {kind} file no longer matches its hash")


def record_receipt(args: argparse.Namespace) -> dict[str, Any]:
    evidence_path = None
    if args.evidence_path:
        evidence = Path(args.evidence_path).expanduser().resolve()
        if not evidence.is_file():
            raise ValueError(f"evidence path does not exist: {evidence}")
        evidence_path = str(evidence)
    payload = {
        "status": args.status,
        "portal": args.portal,
        "confirmation_url": args.confirmation_url,
        "evidence_path": evidence_path,
        "filled_fields": args.filled_field,
        "uploaded_files": args.uploaded_file,
        "blockers": args.blocker,
        "warnings": args.warning,
    }
    handoff_id = urllib.parse.quote(args.handoff_id, safe="")
    return _request_json(
        args.base_url,
        f"/handoffs/{handoff_id}/receipt",
        method="POST",
        payload=payload,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read and update a local JobAuto queue")
    subparsers = parser.add_subparsers(dest="command", required=True)

    next_command = subparsers.add_parser("next", help="Fetch the next ready handoff")
    next_command.add_argument("--base-url", default="http://127.0.0.1:8765")
    next_command.add_argument("--campaign-id", required=True)

    receipt = subparsers.add_parser("receipt", help="Record a handoff receipt")
    receipt.add_argument("--base-url", default="http://127.0.0.1:8765")
    receipt.add_argument("--handoff-id", required=True)
    receipt.add_argument(
        "--status",
        choices=("blocked", "sandbox_verified", "submitted"),
        required=True,
    )
    receipt.add_argument("--portal", required=True)
    receipt.add_argument("--confirmation-url")
    receipt.add_argument("--evidence-path")
    receipt.add_argument("--filled-field", action="append", default=[])
    receipt.add_argument("--uploaded-file", action="append", default=[])
    receipt.add_argument("--blocker", action="append", default=[])
    receipt.add_argument("--warning", action="append", default=[])

    release = subparsers.add_parser("release", help="Release an unused Chrome claim")
    release.add_argument("--base-url", default="http://127.0.0.1:8765")
    release.add_argument("--handoff-id", required=True)
    return parser


def main() -> int:
    try:
        args = _parser().parse_args()
        if args.command == "next":
            result = next_packet(args.base_url, args.campaign_id)
        elif args.command == "receipt":
            result = record_receipt(args)
        else:
            result = release_claim(args.base_url, args.handoff_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
