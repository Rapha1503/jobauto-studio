import errno
import subprocess
from pathlib import Path

import pytest

from jobauto.build import _pdflatex_command, compile_latex, safe_filename


def test_safe_filename_keeps_readable_underscored_name() -> None:
    assert safe_filename("CV Camille / Data Scientist: EDF") == "CV_Camille_Data_Scientist_EDF"


def test_pdflatex_command_uses_miktex_flag_only_when_miktex_is_available(
    tmp_path: Path, monkeypatch
) -> None:
    tex = tmp_path / "cv.tex"
    build = tmp_path / "build"

    monkeypatch.setattr("jobauto.build.shutil.which", lambda _name: None)
    assert "--disable-installer" not in _pdflatex_command(tex, build)

    monkeypatch.setattr(
        "jobauto.build.shutil.which",
        lambda name: "initexmf" if name == "initexmf" else None,
    )
    assert "--disable-installer" in _pdflatex_command(tex, build)


def test_compile_latex_retries_transient_windows_einval(tmp_path: Path, monkeypatch) -> None:
    tex = tmp_path / "cv.tex"
    tex.write_text("document", encoding="utf-8")
    build = tmp_path / "build"
    calls = 0

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EINVAL, "Invalid argument")
        build.mkdir(exist_ok=True)
        (build / "cv.pdf").write_bytes(b"%PDF-test")
        (build / "cv.log").write_text("ok", encoding="utf-8")
        return Result()

    monkeypatch.setattr("jobauto.build.subprocess.run", fake_run)

    pdf, log = compile_latex(tex, build)

    assert calls == 2
    assert pdf.read_bytes() == b"%PDF-test"
    assert log.read_text(encoding="utf-8") == "ok"


def test_compile_latex_has_a_bounded_timeout(tmp_path: Path, monkeypatch) -> None:
    tex = tmp_path / "cv.tex"
    tex.write_text("document", encoding="utf-8")

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("pdflatex", 1)

    monkeypatch.setattr("jobauto.build.subprocess.run", timeout)

    with pytest.raises(RuntimeError, match="timed out after 1 seconds"):
        compile_latex(tex, tmp_path / "build", timeout_seconds=1)


def test_compile_latex_rejects_empty_pdf_output(tmp_path: Path, monkeypatch) -> None:
    tex = tmp_path / "cv.tex"
    tex.write_text("document", encoding="utf-8")
    build = tmp_path / "build"

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(*_args, **_kwargs):
        build.mkdir(exist_ok=True)
        (build / "cv.pdf").write_bytes(b"")
        return Result()

    monkeypatch.setattr("jobauto.build.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="invalid or empty PDF"):
        compile_latex(tex, build)
