from __future__ import annotations

import errno
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path


def safe_filename(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", normalized)).strip("_")


def is_valid_pdf_file(path: Path | None) -> bool:
    if path is None or not path.is_file() or path.stat().st_size < 5:
        return False
    with path.open("rb") as stream:
        return stream.read(5) == b"%PDF-"


def _pdflatex_command(tex_path: Path, build_dir: Path) -> list[str]:
    command = ["pdflatex"]
    if shutil.which("initexmf") is not None:
        command.append("--disable-installer")
    command.extend(
        [
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            f"-output-directory={build_dir}",
            str(tex_path.resolve()),
        ]
    )
    return command


def compile_latex(
    tex_path: Path, build_dir: Path, *, timeout_seconds: int = 60
) -> tuple[Path, Path]:
    build_dir.mkdir(parents=True, exist_ok=True)
    command = _pdflatex_command(tex_path, build_dir)
    for attempt in range(2):
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
            break
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"LaTeX compilation timed out after {timeout_seconds} seconds"
            ) from exc
        except OSError as exc:
            if exc.errno != errno.EINVAL or attempt == 1:
                raise
    log_path = build_dir / f"{tex_path.stem}.log"
    if completed.returncode != 0:
        tail = (completed.stdout + "\n" + completed.stderr)[-4000:]
        raise RuntimeError(f"LaTeX compilation failed:\n{tail}")
    built_pdf = build_dir / f"{tex_path.stem}.pdf"
    if not is_valid_pdf_file(built_pdf):
        raise RuntimeError("LaTeX compilation produced an invalid or empty PDF")
    final_pdf = tex_path.with_suffix(".pdf")
    shutil.copy2(built_pdf, final_pdf)
    if not is_valid_pdf_file(final_pdf):
        raise RuntimeError("LaTeX compilation produced an invalid or empty final PDF")
    return final_pdf, log_path
