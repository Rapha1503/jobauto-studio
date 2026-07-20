from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


class GenerationPhase(StrEnum):
    DISCOVERY = "discovery"
    PROFILE = "profile"
    OFFER_ANALYSIS = "offer_analysis"
    BRIEF_REVIEW = "brief_review"
    BRIEF_REPAIR = "brief_repair"
    PROJECT_LAB = "project_lab"
    CV_WRITER = "cv_writer"
    CV_LATEX_WRITER = "cv_latex_writer"
    LETTER_WRITER = "letter_writer"
    LETTER_REVIEW = "letter_review"
    APPLICATION_WRITER = "application_writer"
    DRAFT = "draft"
    REVIEW = "review"
    FINAL_REVIEW = "final_review"
    BENCHMARK_REVIEW = "benchmark_review"
    REPAIR = "repair"


class CodexResponseError(RuntimeError):
    pass


class CodexOutputValidationError(CodexResponseError):
    def __init__(self, message: str, *, fingerprint: str | None = None) -> None:
        super().__init__(message)
        self.fingerprint = fingerprint or message


def find_codex_executable() -> Path | None:
    for name in ("codex.cmd", "codex"):
        resolved = shutil.which(name)
        if resolved:
            return Path(resolved)
    appdata = os.getenv("APPDATA")
    if appdata:
        candidate = Path(appdata) / "npm" / "codex.cmd"
        if candidate.is_file():
            return candidate
    return None


class CodexClient:
    def __init__(
        self,
        executable: Path,
        cwd: Path,
        *,
        model: str | None = None,
        fallback_models: tuple[str, ...] = (),
        runner: Any = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
        timeout_seconds: int = 900,
        max_schema_attempts: int = 3,
    ) -> None:
        self.executable = executable
        self.cwd = cwd
        self.model = model
        self.fallback_models = tuple(
            candidate for candidate in fallback_models if candidate and candidate != model
        )
        self._runner = runner
        self._sleeper = sleeper
        self.timeout_seconds = timeout_seconds
        self.max_schema_attempts = max_schema_attempts
        self.telemetry_log: list[dict[str, Any]] = []
        self.event_callback: Callable[[dict[str, Any]], None] | None = None

    @classmethod
    def default(
        cls,
        cwd: Path | None = None,
        model: str | None = None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> CodexClient:
        executable = find_codex_executable()
        if executable is None:
            raise RuntimeError("Codex CLI missing; install or expose codex.cmd in PATH")
        configured_fallbacks = os.getenv(
            "JOBAUTO_CODEX_FALLBACK_MODELS",
            "gpt-5.6-terra",
        )
        client = cls(
            executable=executable,
            cwd=cwd or Path.cwd(),
            model=model,
            fallback_models=tuple(
                item.strip() for item in configured_fallbacks.split(",") if item.strip()
            ),
        )
        client.event_callback = event_callback
        return client

    def complete_json(
        self,
        prompt: str,
        response_model: type[T],
        phase: GenerationPhase,
        **_kwargs: Any,
    ) -> T:
        temp_root = self.cwd / "tmp" / "codex_client"
        temp_root.mkdir(parents=True, exist_ok=True)
        full_prompt = self._wrap_prompt(prompt, response_model, phase)
        last_error: CodexResponseError | None = None
        active_model = self.model
        next_fallback = 0
        call_id = uuid4().hex
        for attempt in range(self.max_schema_attempts):
            started = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix=f"{phase.value}_", dir=temp_root) as temp_name:
                temp_dir = Path(temp_name)
                output_path = temp_dir / "last_message.json"
                command = [str(self.executable)]
                if phase is GenerationPhase.DISCOVERY:
                    command.append("--search")
                command.extend(
                    [
                        "exec",
                        "--cd",
                        str(self.cwd),
                        "--sandbox",
                        "read-only",
                        "--skip-git-repo-check",
                        "--ephemeral",
                        "--ignore-user-config",
                        "--color",
                        "never",
                        "--output-last-message",
                        str(output_path),
                        "-",
                    ]
                )
                if active_model:
                    exec_index = command.index("exec")
                    command[exec_index + 1 : exec_index + 1] = ["--model", active_model]
                attempt_prompt = (
                    self._retry_prompt(full_prompt, response_model, last_error)
                    if isinstance(last_error, CodexOutputValidationError)
                    else full_prompt
                )
                self._emit_event(
                    {
                        "model": "codex-cli",
                        "call_id": call_id,
                        "codex_model": active_model or "default",
                        "phase": phase.value,
                        "status": "running",
                        "attempt": attempt + 1,
                        "input_sha256": hashlib.sha256(attempt_prompt.encode("utf-8")).hexdigest(),
                    }
                )
                try:
                    completed = self._runner(
                        args=command,
                        input=attempt_prompt,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=self.timeout_seconds,
                    )
                except subprocess.TimeoutExpired as exc:
                    self._record_telemetry(
                        phase=phase,
                        attempt=attempt + 1,
                        prompt=attempt_prompt,
                        raw_output="",
                        started=started,
                        status="failed",
                        codex_model=active_model,
                        call_id=call_id,
                        pipeline_outcome="transport_failed",
                        rejection_reason=f"timeout after {self.timeout_seconds}s",
                    )
                    raise CodexResponseError(
                        f"Codex CLI timed out after {self.timeout_seconds}s"
                    ) from exc
                if completed.returncode != 0:
                    failure_output = completed.stderr or completed.stdout or ""
                    self._record_telemetry(
                        phase=phase,
                        attempt=attempt + 1,
                        prompt=attempt_prompt,
                        raw_output=failure_output,
                        started=started,
                        status="failed",
                        codex_model=active_model,
                        call_id=call_id,
                        pipeline_outcome="transport_failed",
                        rejection_reason=failure_output or f"exit code {completed.returncode}",
                    )
                    transport_error = CodexResponseError(
                        "Codex CLI failed: "
                        + (failure_output or f"exit code {completed.returncode}")
                    )
                    if (
                        self._is_transient_transport_failure(failure_output)
                        and attempt + 1 < self.max_schema_attempts
                    ):
                        last_error = transport_error
                        if next_fallback < len(self.fallback_models):
                            active_model = self.fallback_models[next_fallback]
                            next_fallback += 1
                        self._sleeper(min(2.0**attempt, 8.0))
                        continue
                    raise transport_error
                if not output_path.is_file():
                    self._record_telemetry(
                        phase=phase,
                        attempt=attempt + 1,
                        prompt=attempt_prompt,
                        raw_output="",
                        started=started,
                        status="failed",
                        codex_model=active_model,
                        call_id=call_id,
                        pipeline_outcome="transport_failed",
                        rejection_reason="Codex CLI did not write an output message",
                    )
                    raise CodexResponseError("Codex CLI did not write an output message")
                raw_output = output_path.read_text(encoding="utf-8")
                try:
                    parsed = self._parse_model(raw_output, response_model)
                except CodexResponseError as exc:
                    self._record_telemetry(
                        phase=phase,
                        attempt=attempt + 1,
                        prompt=attempt_prompt,
                        raw_output=raw_output,
                        started=started,
                        status="rejected",
                        codex_model=active_model,
                        call_id=call_id,
                        pipeline_outcome="schema_rejected",
                        rejection_reason=str(exc),
                    )
                    last_error = exc
                    if attempt + 1 < self.max_schema_attempts:
                        continue
                    raise
                self._record_telemetry(
                    phase=phase,
                    attempt=attempt + 1,
                    prompt=attempt_prompt,
                    raw_output=raw_output,
                    started=started,
                    status="succeeded",
                    codex_model=active_model,
                    call_id=call_id,
                )
                return parsed
        raise last_error or CodexResponseError("Codex did not return valid JSON")

    def _record_telemetry(
        self,
        *,
        phase: GenerationPhase,
        attempt: int,
        prompt: str,
        raw_output: str,
        started: float,
        status: str,
        codex_model: str | None,
        call_id: str,
        pipeline_outcome: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        prompt_tokens_estimate = estimate_tokens(prompt)
        completion_tokens_estimate = estimate_tokens(raw_output)
        event: dict[str, Any] = {
            "model": "codex-cli",
            "call_id": call_id,
            "codex_model": codex_model or "default",
            "phase": phase.value,
            "status": status,
            "input_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "output_sha256": hashlib.sha256(raw_output.encode("utf-8")).hexdigest(),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "prompt_chars": len(prompt),
            "completion_chars": len(raw_output),
            "total_tokens": 0,
            "prompt_tokens_estimate": prompt_tokens_estimate,
            "completion_tokens_estimate": completion_tokens_estimate,
            "total_tokens_estimate": prompt_tokens_estimate + completion_tokens_estimate,
            "attempt": attempt,
        }
        if pipeline_outcome is not None:
            event["pipeline_outcome"] = pipeline_outcome
        if rejection_reason is not None:
            event["rejection_reason"] = rejection_reason
        self.telemetry_log.append(event)
        self._emit_event(event)

    @staticmethod
    def _is_transient_transport_failure(output: str) -> bool:
        normalized = output.casefold()
        return any(
            marker in normalized
            for marker in (
                "at capacity",
                "rate limit",
                "too many requests",
                "temporarily unavailable",
                "service unavailable",
                "internal server error",
                "connection reset",
                "connection refused",
                "request timed out",
            )
        )

    def _emit_event(self, event: dict[str, Any]) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(dict(event))
        except OSError:
            return

    @staticmethod
    def _wrap_prompt(prompt: str, response_model: type[BaseModel], phase: GenerationPhase) -> str:
        if phase == GenerationPhase.DISCOVERY:
            role_contract = (
                "You are JobAuto's Codex web research agent.\n"
                "Use web search and open authoritative job pages to complete the task.\n"
                "Do not edit files, run shell commands, or ask questions.\n"
            )
        else:
            role_contract = (
                "You are JobAuto's Codex-only document adaptation engine.\n"
                "Do not edit files, run shell commands, browse, or ask questions.\n"
                "Use only the context inside this prompt.\n"
            )
        schema_contract = ""
        if "## OUTPUT JSON SCHEMA" not in prompt.upper():
            schema_contract = (
                "Output JSON schema:\n"
                f"{json.dumps(response_model.model_json_schema(), ensure_ascii=False)}\n\n"
            )
        return (
            role_contract
            + "Return only valid JSON matching the provided output schema. No markdown.\n\n"
            f"Generation phase: {phase.value}\n"
            f"Output model: {response_model.__name__}\n\n"
            f"{schema_contract}"
            "User/task prompt starts below.\n"
            "-----\n"
            f"{prompt}\n"
            "-----\n"
        )

    @staticmethod
    def _retry_prompt(
        original_prompt: str,
        response_model: type[BaseModel],
        error: CodexResponseError | None,
    ) -> str:
        return (
            "Previous attempt returned invalid JSON. Retry the same task, but output only one "
            "compact, syntactically valid JSON object matching the schema. Escape all quotes and "
            "newlines inside strings. Do not include markdown, comments, or explanations.\n"
            f"Output model: {response_model.__name__}\n"
            f"Validation error: {error}\n\n"
            f"{original_prompt}"
        )

    @classmethod
    def _parse_model(cls, raw_output: str, response_model: type[T]) -> T:
        json_text = cls._extract_json(raw_output)
        try:
            return response_model.model_validate_json(json_text)
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                fingerprint = json.dumps(
                    [(tuple(error["loc"]), error["type"]) for error in exc.errors()],
                    ensure_ascii=False,
                    sort_keys=True,
                )
            else:
                fingerprint = type(exc).__name__
            raise CodexOutputValidationError(
                f"Codex did not return valid JSON for {response_model.__name__}: {exc}",
                fingerprint=fingerprint,
            ) from exc

    @staticmethod
    def _extract_json(raw_output: str) -> str:
        text = raw_output.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return text[start : end + 1]
        raise CodexOutputValidationError(
            "Codex did not return valid JSON",
            fingerprint="missing_json_object",
        )


def estimate_tokens(text: str) -> int:
    """Cheap token estimate for Codex CLI runs where API billing tokens are unavailable."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)
