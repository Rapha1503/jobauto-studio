from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobauto.candidate_profile import CvBackend
from jobauto.candidate_snapshot import CandidateProfileRepository
from jobauto.profile_extraction import CandidateProfileExtraction
from jobauto.studio.app import create_studio_app
from jobauto.studio.pdf_imports import PdfImportStore


def text_pdf_bytes(*lines: str) -> bytes:
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    commands = ["BT /F1 12 Tf 14 TL 72 720 Td"]
    for index, line in enumerate(escaped):
        if index:
            commands.append("T*")
        commands.append(f"({line}) Tj")
    commands.append("ET")
    stream = " ".join(commands).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length "
        + str(len(stream)).encode("ascii")
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def test_pdf_import_preserves_original_and_extracts_selectable_text(tmp_path: Path) -> None:
    source = text_pdf_bytes(
        "Alex Morgan - Regulatory Affairs Specialist",
        "Prepared medical-device submissions and ISO 13485 evidence.",
    )

    record = PdfImportStore(tmp_path).create(source, filename="alex-cv.pdf")

    assert record.page_count == 1
    assert record.source_path.read_bytes() == source
    assert "Regulatory Affairs Specialist" in PdfImportStore(tmp_path).pages(record.import_id)[0]


def test_pdf_import_rejects_a_file_without_selectable_cv_text(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="too little selectable text"):
        PdfImportStore(tmp_path).create(text_pdf_bytes("x"), filename="scan.pdf")


class PdfProfileExtractor:
    def extract_pdf_pages(self, pages: list[str]) -> CandidateProfileExtraction:
        assert "Regulatory Affairs" in pages[0]
        return CandidateProfileExtraction.model_validate(
            {
                "locale": "en-GB",
                "identity": {
                    "first_name": "Alex",
                    "last_name": "Morgan",
                    "email": "alex.morgan@example.test",
                    "headline": "Regulatory Affairs Specialist",
                    "source_block_ids": ["page_1"],
                },
                "summary": "Regulatory specialist working on medical-device submissions.",
                "summary_source_block_ids": ["page_1"],
                "experiences": [
                    {
                        "experience_id": "northbridge",
                        "organization": "Northbridge Health",
                        "role": "Regulatory Affairs Associate",
                        "facts": ["Prepared submission evidence under ISO 13485."],
                        "source_block_ids": ["page_1"],
                    }
                ],
            }
        )


def test_pdf_upload_becomes_a_reviewable_generated_profile(tmp_path: Path) -> None:
    source = text_pdf_bytes(
        "Alex Morgan - Regulatory Affairs Specialist",
        "Prepared medical-device submissions and ISO 13485 evidence.",
    )
    client = TestClient(
        create_studio_app(state_root=tmp_path, profile_extractor=PdfProfileExtractor())
    )

    imported = client.post(
        "/api/pdf-imports",
        content=source,
        headers={"X-Filename": "alex-cv.pdf", "Content-Type": "application/pdf"},
    )

    assert imported.status_code == 202, imported.text
    payload = imported.json()
    status = client.get(payload["status_url"])
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    draft = client.get(status.json()["page_url"])
    assert draft.status_code == 200
    assert "Filled automatically from selectable PDF text" in draft.text
    draft_id = status.json()["draft_id"]
    draft_payload = client.get(f"/api/candidate-drafts/{draft_id}").json()
    assert draft_payload["origin"] == "pdf"
    assert draft_payload["source_document_id"] == payload["import_id"]
    assert client.get(payload["preview_url"]).content == source

    assert client.post(f"/api/candidate-drafts/{draft_id}/validate").status_code == 200
    exported = client.post(f"/api/candidate-drafts/{draft_id}/export")
    assert exported.status_code == 201, exported.text
    snapshot = CandidateProfileRepository(tmp_path / "candidate-profiles").load_snapshot(
        Path(exported.json()["profile_path"])
    )
    assert snapshot.profile.cv_backend is CvBackend.GENERATED_TEMPLATE
