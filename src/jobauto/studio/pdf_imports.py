from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pypdf import PdfReader

MAX_PDF_SOURCE_BYTES = 10_000_000
MAX_PDF_PAGES = 50


class PdfImportRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    import_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    filename: str
    created_at: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_path: Path
    text_path: Path
    page_count: int = Field(ge=1)
    extracted_character_count: int = Field(ge=1)


class PdfImportStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, source: bytes, *, filename: str) -> PdfImportRecord:
        if not filename.lower().endswith(".pdf"):
            raise ValueError("PDF filename must end with .pdf")
        if len(source) > MAX_PDF_SOURCE_BYTES:
            raise ValueError("PDF CV exceeds the 10 MB limit")
        if not source.startswith(b"%PDF-"):
            raise ValueError("Uploaded file is not a PDF")
        try:
            reader = PdfReader(io.BytesIO(source), strict=False)
            if reader.is_encrypted:
                raise ValueError("Encrypted PDF CVs are not supported")
            if not reader.pages:
                raise ValueError("PDF CV contains no pages")
            if len(reader.pages) > MAX_PDF_PAGES:
                raise ValueError(f"PDF CV exceeds the {MAX_PDF_PAGES}-page limit")
            pages = [
                "\n".join((page.extract_text() or "").splitlines()).strip() for page in reader.pages
            ]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF CV could not be read") from exc
        extracted_character_count = sum(len(page) for page in pages)
        if extracted_character_count < 40:
            raise ValueError(
                "PDF contains too little selectable text. Use the manual editor for a scanned CV."
            )

        import_id = uuid4().hex
        import_root = self.root / import_id
        import_root.mkdir(parents=True)
        source_path = import_root / "original.pdf"
        text_path = import_root / "pages.json"
        source_path.write_bytes(source)
        text_path.write_text(
            json.dumps(pages, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        record = PdfImportRecord(
            import_id=import_id,
            filename=Path(filename).name,
            created_at=datetime.now(UTC).isoformat(),
            source_sha256=hashlib.sha256(source).hexdigest(),
            source_path=source_path,
            text_path=text_path,
            page_count=len(pages),
            extracted_character_count=extracted_character_count,
        )
        self._record_path(import_id).write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return record

    def get(self, import_id: str) -> PdfImportRecord:
        path = self._record_path(import_id)
        if not path.is_file():
            raise FileNotFoundError(import_id)
        return PdfImportRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def pages(self, import_id: str) -> list[str]:
        return list(json.loads(self.get(import_id).text_path.read_text(encoding="utf-8")))

    def _record_path(self, import_id: str) -> Path:
        if not import_id or any(character not in "0123456789abcdef" for character in import_id):
            raise FileNotFoundError(import_id)
        path = (self.root / import_id / "record.json").resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise FileNotFoundError(import_id) from exc
        return path
