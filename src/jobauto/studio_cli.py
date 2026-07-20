from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

import typer
import uvicorn

from jobauto.release_audit import audit_release_path, configured_deny_terms
from jobauto.studio.app import create_studio_app

app = typer.Typer(no_args_is_help=True, help="JobAuto Studio local application")


@app.command()
def studio(
    host: str = typer.Option("127.0.0.1", help="Local bind address."),
    port: int = typer.Option(8765, min=1, max=65535, help="Local HTTP port."),
    state_root: Path | None = typer.Option(None, help="Persistent local Studio state."),
    profiles_root: Path | None = typer.Option(None, help="Optional profile library."),
    codex_model: str = typer.Option(
        os.getenv("JOBAUTO_CODEX_MODEL", "gpt-5.6-sol"),
        "--codex-model",
        help="Codex model used for sourcing, strategy, writing, review, and repair.",
    ),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser"),
) -> None:
    """Start the local Studio UI."""
    url = f"http://{host}:{port}"
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        create_studio_app(
            state_root=state_root,
            profiles_root=profiles_root,
            codex_model=codex_model,
        ),
        host=host,
        port=port,
    )


@app.command("audit-release")
def audit_release(
    path: Path = typer.Argument(..., exists=True, readable=True),
    deny_term: list[str] | None = typer.Option(None, "--deny-term"),
) -> None:
    """Fail when a source tree or wheel contains personal data or secrets."""
    leaks = audit_release_path(path, deny_terms=configured_deny_terms(tuple(deny_term or ())))
    if leaks:
        for leak in leaks:
            typer.echo(f"{leak.source}: {leak.kind}: {leak.excerpt}")
        raise typer.Exit(code=1)
    typer.echo("release audit: clean")
