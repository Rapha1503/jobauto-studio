from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def render_pdf_first_page(pdf_path: Path, output_path: Path) -> Path:
    """Render a stable first-page preview for the Studio document comparator."""
    source = pdf_path.expanduser().resolve()
    target = output_path.expanduser().resolve()
    if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return target

    executable = shutil.which("pdftocairo")
    if executable is None:
        raise RuntimeError("PDF preview requires pdftocairo (Poppler or MiKTeX)")
    target.parent.mkdir(parents=True, exist_ok=True)
    prefix = target.with_suffix("")
    try:
        completed = subprocess.run(
            [
                executable,
                "-png",
                "-f",
                "1",
                "-l",
                "1",
                "-singlefile",
                "-r",
                "144",
                str(source),
                str(prefix),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("PDF preview rendering timed out") from exc
    if completed.returncode != 0 or not target.is_file():
        detail = (completed.stderr or completed.stdout).strip()[-500:]
        raise RuntimeError(f"PDF preview rendering failed: {detail or 'unknown error'}")
    return target
