import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel

from jobauto.codex_client import (
    CodexClient,
    CodexOutputValidationError,
    CodexRoute,
    GenerationPhase,
    find_codex_executable,
)


class MiniResponse(BaseModel):
    value: str


def test_find_codex_executable_prefers_cmd_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_which(name: str) -> str | None:
        return {"codex.cmd": r"C:\Codex\codex.cmd"}.get(name)

    monkeypatch.setattr("jobauto.codex_client.shutil.which", fake_which)

    assert find_codex_executable() == Path(r"C:\Codex\codex.cmd")


def test_complete_json_uses_stdin_schema_prompt_and_last_message(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_runner(**kwargs):
        command = kwargs["args"]
        seen["command"] = command
        seen["input"] = kwargs["input"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )

    result = client.complete_json("Analyse cette offre.", MiniResponse, GenerationPhase.PROFILE)

    assert result == MiniResponse(value="ok")
    command = seen["command"]
    assert command[:2] == [r"C:\Codex\codex.cmd", "exec"]
    assert "-" in command
    assert "--output-schema" not in command
    assert "--output-last-message" in command
    assert "Generation phase: profile" in seen["input"]
    assert "Output JSON schema:" in seen["input"]
    assert "Analyse cette offre." in seen["input"]
    assert client.telemetry_log[0]["model"] == "codex-cli"
    assert client.telemetry_log[0]["codex_model"] == "default"
    assert client.telemetry_log[0]["phase"] == "profile"
    assert client.telemetry_log[0]["status"] == "succeeded"
    assert len(client.telemetry_log[0]["input_sha256"]) == 64
    assert len(client.telemetry_log[0]["output_sha256"]) == 64
    assert client.telemetry_log[0]["prompt_tokens_estimate"] > 0
    assert client.telemetry_log[0]["completion_tokens_estimate"] > 0
    assert client.telemetry_log[0]["total_tokens_estimate"] == (
        client.telemetry_log[0]["prompt_tokens_estimate"]
        + client.telemetry_log[0]["completion_tokens_estimate"]
    )


def test_wrap_prompt_does_not_duplicate_an_embedded_output_schema() -> None:
    embedded = '## OUTPUT JSON SCHEMA\n{"type":"object"}\n\nReturn JSON only.'

    wrapped = CodexClient._wrap_prompt(embedded, MiniResponse, GenerationPhase.CV_WRITER)

    assert wrapped.count("## OUTPUT JSON SCHEMA") == 1
    assert "Output JSON schema:" not in wrapped
    assert "Output model: MiniResponse" in wrapped


def test_complete_json_emits_live_start_and_terminal_events(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []

    def fake_runner(**kwargs):
        command = kwargs["args"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )
    client.event_callback = events.append

    client.complete_json("Prompt", MiniResponse, GenerationPhase.CV_WRITER)

    assert [event["status"] for event in events] == ["running", "succeeded"]
    assert all(event["phase"] == "cv_writer" for event in events)
    assert events[0]["attempt"] == 1
    assert len(str(events[0]["input_sha256"])) == 64
    assert events[0]["call_id"] == events[1]["call_id"]
    assert len(str(events[0]["call_id"])) == 32


def test_complete_json_passes_configured_model(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_runner(**kwargs):
        command = kwargs["args"]
        seen["command"] = command
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        model="gpt-5.5-codex",
        runner=fake_runner,
    )

    client.complete_json("Prompt", MiniResponse, GenerationPhase.REVIEW)

    command = seen["command"]
    assert command[2:4] == ["--model", "gpt-5.5-codex"]
    assert client.telemetry_log[0]["codex_model"] == "gpt-5.5-codex"


def test_complete_json_can_route_one_phase_to_a_model_and_reasoning_effort(
    tmp_path: Path,
) -> None:
    seen: dict[str, object] = {}

    def fake_runner(**kwargs):
        command = kwargs["args"]
        seen["command"] = command
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        model="gpt-5.6-sol",
        reasoning_effort="medium",
        phase_routes={
            GenerationPhase.CV_LATEX_WRITER: CodexRoute(
                model="gpt-5.6-luna",
                reasoning_effort="high",
            )
        },
        runner=fake_runner,
    )

    client.complete_json("Prompt", MiniResponse, GenerationPhase.CV_LATEX_WRITER)

    command = seen["command"]
    assert command[2:6] == [
        "--config",
        'model_reasoning_effort="high"',
        "--model",
        "gpt-5.6-luna",
    ]
    assert client.telemetry_log[0]["codex_model"] == "gpt-5.6-luna"
    assert client.telemetry_log[0]["reasoning_effort"] == "high"


def test_discovery_phase_explicitly_enables_web_research(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_runner(**kwargs):
        seen["prompt"] = kwargs["input"]
        command = kwargs["args"]
        seen["command"] = command
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )

    client.complete_json("Find current offers.", MiniResponse, GenerationPhase.DISCOVERY)

    command = seen["command"]
    assert command[1:3] == ["--search", "exec"]
    assert "Use web search and open authoritative job pages" in seen["prompt"]
    assert "Do not edit files, run shell commands" in seen["prompt"]
    assert "Do not edit files, run shell commands, browse" not in seen["prompt"]


def test_non_discovery_phase_does_not_enable_web_search(tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_runner(**kwargs):
        command = kwargs["args"]
        seen["command"] = command
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )

    client.complete_json("Review documents.", MiniResponse, GenerationPhase.REVIEW)

    assert "--search" not in seen["command"]


def test_complete_json_raises_on_invalid_json(tmp_path: Path) -> None:
    def fake_runner(**kwargs):
        command = kwargs["args"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )

    with pytest.raises(CodexOutputValidationError, match="valid JSON"):
        client.complete_json("Prompt", MiniResponse, GenerationPhase.REVIEW)


def test_complete_json_retries_once_after_invalid_json(tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_runner(**kwargs):
        calls.append(kwargs["input"])
        command = kwargs["args"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        if len(calls) == 1:
            output_path.write_text('{"value": "missing end"', encoding="utf-8")
        else:
            output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )

    result = client.complete_json("Prompt", MiniResponse, GenerationPhase.LETTER_WRITER)

    assert result == MiniResponse(value="ok")
    assert len(calls) == 2
    assert "Previous attempt returned invalid JSON" in calls[1]
    assert [event["status"] for event in client.telemetry_log] == [
        "rejected",
        "succeeded",
    ]
    assert len({event["call_id"] for event in client.telemetry_log}) == 1
    assert [event["attempt"] for event in client.telemetry_log] == [1, 2]
    assert client.telemetry_log[0]["pipeline_outcome"] == "schema_rejected"
    assert "valid JSON" in client.telemetry_log[0]["rejection_reason"]


def test_complete_json_allows_a_second_schema_correction(tmp_path: Path) -> None:
    calls = 0

    def fake_runner(**kwargs):
        nonlocal calls
        calls += 1
        command = kwargs["args"]
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text(
            '{"wrong": true}' if calls < 3 else '{"value": "ok"}',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
    )

    assert client.complete_json(
        "Prompt", MiniResponse, GenerationPhase.OFFER_ANALYSIS
    ) == MiniResponse(value="ok")
    assert calls == 3


def test_complete_json_retries_transient_capacity_with_fallback_model(tmp_path: Path) -> None:
    commands: list[list[str]] = []
    delays: list[float] = []

    def fake_runner(**kwargs):
        command = kwargs["args"]
        commands.append(command)
        if len(commands) == 1:
            return subprocess.CompletedProcess(
                command,
                1,
                "",
                "ERROR: Selected model is at capacity. Please try a different model.",
            )
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text('{"value": "ok"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        model="gpt-5.6-sol",
        fallback_models=("gpt-5.6-terra",),
        runner=fake_runner,
        sleeper=delays.append,
    )

    result = client.complete_json("Prompt", MiniResponse, GenerationPhase.OFFER_ANALYSIS)

    assert result == MiniResponse(value="ok")
    assert commands[0][2:4] == ["--model", "gpt-5.6-sol"]
    assert commands[1][2:4] == ["--model", "gpt-5.6-terra"]
    assert delays == [1.0]
    assert [event["status"] for event in client.telemetry_log] == ["failed", "succeeded"]
    assert [event["codex_model"] for event in client.telemetry_log] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]


def test_complete_json_does_not_retry_non_transient_cli_failure(tmp_path: Path) -> None:
    calls = 0

    def fake_runner(**kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(kwargs["args"], 2, "", "invalid option")

    client = CodexClient(
        executable=Path(r"C:\Codex\codex.cmd"),
        cwd=tmp_path,
        runner=fake_runner,
        sleeper=lambda _delay: None,
    )

    with pytest.raises(RuntimeError, match="invalid option"):
        client.complete_json("Prompt", MiniResponse, GenerationPhase.OFFER_ANALYSIS)

    assert calls == 1
