from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from jobauto.build import compile_latex, is_valid_pdf_file
from jobauto.latex_cv_source import (
    LatexCvMapping,
    TexBlockCorrection,
    analyze_latex_cv,
    corrected_mapping,
)


class TexImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    filename: str
    created_at: str
    source_path: Path
    mapping_path: Path
    pdf_path: Path | None = None
    log_path: Path | None = None
    compilation_status: str
    compilation_error: str | None = None


class TexImportStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, source: bytes, *, filename: str) -> TexImportRecord:
        mapping = analyze_latex_cv(source, filename=filename)
        import_id = uuid4().hex
        import_root = self.root / import_id
        import_root.mkdir(parents=True)
        source_path = import_root / "original.tex"
        mapping_path = import_root / "cv_map.json"
        source_path.write_bytes(source)
        mapping.write(mapping_path)
        record = TexImportRecord(
            import_id=import_id,
            filename=mapping.filename,
            created_at=datetime.now(UTC).isoformat(),
            source_path=source_path,
            mapping_path=mapping_path,
            compilation_status="pending",
        )
        try:
            pdf_path, log_path = compile_latex(source_path, import_root / "build")
            record = record.model_copy(
                update={
                    "pdf_path": pdf_path,
                    "log_path": log_path,
                    "compilation_status": "compiled",
                }
            )
        except (OSError, RuntimeError) as exc:
            record = record.model_copy(
                update={
                    "compilation_status": "failed",
                    "compilation_error": str(exc)[-4000:],
                }
            )
        self._write_record(record)
        return record

    def get(self, import_id: str) -> TexImportRecord:
        record_path = self._record_path(import_id)
        if not record_path.is_file():
            raise FileNotFoundError(import_id)
        record = TexImportRecord.model_validate_json(record_path.read_text(encoding="utf-8"))
        if record.compilation_status == "compiled" and not is_valid_pdf_file(record.pdf_path):
            record = record.model_copy(
                update={
                    "pdf_path": None,
                    "compilation_status": "failed",
                    "compilation_error": "Stored LaTeX compilation has no valid PDF output.",
                }
            )
            self._write_record(record)
        return record

    def mapping(self, import_id: str) -> LatexCvMapping:
        return LatexCvMapping.load(self.get(import_id).mapping_path)

    def source(self, import_id: str) -> bytes:
        return self.get(import_id).source_path.read_bytes()

    def correct_mapping(
        self, import_id: str, corrections: list[TexBlockCorrection]
    ) -> LatexCvMapping:
        record = self.get(import_id)
        mapping = LatexCvMapping.load(record.mapping_path)
        updated = corrected_mapping(record.source_path.read_bytes(), mapping, corrections)
        updated.write(record.mapping_path)
        return updated

    def _record_path(self, import_id: str) -> Path:
        if not import_id or any(character not in "0123456789abcdef" for character in import_id):
            raise FileNotFoundError(import_id)
        path = (self.root / import_id / "record.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(import_id) from exc
        return path

    def _write_record(self, record: TexImportRecord) -> None:
        self._record_path(record.import_id).write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
