from __future__ import annotations

from pathlib import Path

from jobauto.studio.tex_imports import TexImportStore

SOURCE = rb"""\documentclass{article}
\newcommand{\cvsection}[1]{\section*{#1}}
\begin{document}
Alex Morgan
\cvsection{Profile}
Data engineer.
\cvsection{Skills}
Python, SQL
\end{document}
"""


def test_tex_import_store_preserves_original_bytes_and_mapping(tmp_path: Path, monkeypatch) -> None:
    def fake_compile(source_path: Path, build_dir: Path) -> tuple[Path, Path]:
        build_dir.mkdir(parents=True)
        pdf_path = source_path.with_suffix(".pdf")
        log_path = build_dir / "original.log"
        pdf_path.write_bytes(b"%PDF-test")
        log_path.write_text("ok", encoding="utf-8")
        return pdf_path, log_path

    monkeypatch.setattr("jobauto.studio.tex_imports.compile_latex", fake_compile)
    store = TexImportStore(tmp_path / "imports")

    record = store.create(SOURCE, filename="trusted-cv.tex")

    assert record.compilation_status == "compiled"
    assert store.source(record.import_id) == SOURCE
    assert store.mapping(record.import_id).filename == "trusted-cv.tex"
    assert record.pdf_path is not None and record.pdf_path.read_bytes() == b"%PDF-test"


def test_tex_import_store_keeps_failed_import_observable(tmp_path: Path, monkeypatch) -> None:
    def fail_compile(_source_path: Path, _build_dir: Path) -> tuple[Path, Path]:
        raise RuntimeError("missing local style")

    monkeypatch.setattr("jobauto.studio.tex_imports.compile_latex", fail_compile)
    store = TexImportStore(tmp_path / "imports")

    record = store.create(SOURCE, filename="dependent-cv.tex")

    assert record.compilation_status == "failed"
    assert record.compilation_error == "missing local style"
    assert store.source(record.import_id) == SOURCE


def test_tex_import_store_reclassifies_legacy_empty_pdf(tmp_path: Path, monkeypatch) -> None:
    def fake_compile(source_path: Path, build_dir: Path) -> tuple[Path, Path]:
        build_dir.mkdir(parents=True)
        pdf_path = source_path.with_suffix(".pdf")
        log_path = build_dir / "original.log"
        pdf_path.write_bytes(b"%PDF-test")
        log_path.write_text("ok", encoding="utf-8")
        return pdf_path, log_path

    monkeypatch.setattr("jobauto.studio.tex_imports.compile_latex", fake_compile)
    store = TexImportStore(tmp_path / "imports")
    record = store.create(SOURCE, filename="trusted-cv.tex")
    assert record.pdf_path is not None
    record.pdf_path.write_bytes(b"")

    reloaded = store.get(record.import_id)

    assert reloaded.compilation_status == "failed"
    assert reloaded.pdf_path is None
    assert reloaded.compilation_error == "Stored LaTeX compilation has no valid PDF output."
