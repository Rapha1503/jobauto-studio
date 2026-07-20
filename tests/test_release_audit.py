import io
import struct
import tarfile
import zlib
from pathlib import Path
from zipfile import ZipFile

from pypdf import PdfWriter

from jobauto.release_audit import audit_release_path


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _minimal_png(*metadata: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + b"".join(metadata)
        + _png_chunk(b"IEND", b"")
    )


def test_release_audit_scans_every_text_file(tmp_path: Path) -> None:
    (tmp_path / "safe.py").write_text("owner = 'Demo Candidate'", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "leak.md").write_text("Forbidden Person", encoding="utf-8")

    leaks = audit_release_path(tmp_path, deny_terms=("Forbidden Person",))

    assert [(item.source, item.kind) for item in leaks] == [("nested/leak.md", "deny_term")]


def test_release_audit_scans_wheel_members(tmp_path: Path) -> None:
    wheel = tmp_path / "jobauto-test.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("jobauto/safe.py", "email = 'demo@example.test'")
        archive.writestr("jobauto/leak.py", "token = '" + "sk-" + "1234567890abcdef'")

    leaks = audit_release_path(wheel)

    assert [(item.source, item.kind) for item in leaks] == [("jobauto/leak.py", "api_secret")]


def test_release_audit_scans_pdf_metadata_inside_wheel(tmp_path: Path) -> None:
    private_email = "private@" + "candidate.invalid"
    pdf = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Subject": f"Contact {private_email}"})
    writer.write(pdf)
    wheel = tmp_path / "jobauto-test.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("jobauto/demo.pdf", pdf.getvalue())

    leaks = audit_release_path(wheel)

    assert [(item.source, item.kind) for item in leaks] == [("jobauto/demo.pdf", "email")]


def test_release_audit_scans_png_text_metadata(tmp_path: Path) -> None:
    private_email = b"private@" + b"candidate.invalid"
    png = _minimal_png(_png_chunk(b"tEXt", b"Comment\x00" + private_email))
    path = tmp_path / "proof.png"
    path.write_bytes(png)

    leaks = audit_release_path(path)

    assert [(item.source, item.kind) for item in leaks] == [("proof.png", "email")]


def test_release_audit_rejects_truncated_or_corrupt_png(tmp_path: Path) -> None:
    truncated = tmp_path / "truncated.png"
    truncated.write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 20) + b"tEXtshort")
    corrupt = tmp_path / "corrupt.png"
    payload = bytearray(_minimal_png())
    payload[-1] ^= 0xFF
    corrupt.write_bytes(payload)

    assert [(item.source, item.kind) for item in audit_release_path(truncated)] == [
        ("truncated.png", "unreadable_png")
    ]
    assert [(item.source, item.kind) for item in audit_release_path(corrupt)] == [
        ("corrupt.png", "unreadable_png")
    ]


def test_release_audit_scans_utf16_jpeg_metadata(tmp_path: Path) -> None:
    private_email = "private@" + "candidate.invalid"
    metadata = b"Exif\x00\x00" + private_email.encode("utf-16-le")
    segment = b"\xff\xe1" + struct.pack(">H", len(metadata) + 2) + metadata
    path = tmp_path / "proof.jpg"
    path.write_bytes(b"\xff\xd8" + segment + b"\xff\xd9")

    leaks = audit_release_path(path)

    assert [(item.source, item.kind) for item in leaks] == [("proof.jpg", "email")]


def test_release_audit_only_ignores_valid_pdf_timestamps(tmp_path: Path) -> None:
    valid = tmp_path / "valid.txt"
    valid.write_text("CreationDate D:20260718162551", encoding="utf-8")
    invalid = tmp_path / "invalid.txt"
    invalid.write_text("Contact +20 " + "123456" + "789012", encoding="utf-8")

    assert audit_release_path(valid) == []
    assert [(item.source, item.kind) for item in audit_release_path(invalid)] == [
        ("invalid.txt", "phone")
    ]


def test_release_audit_scans_sdist_members_instead_of_compressed_bytes(tmp_path: Path) -> None:
    sdist = tmp_path / "jobauto-test.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        safe = b"email = 'demo@example.test'"
        safe_info = tarfile.TarInfo("jobauto/safe.py")
        safe_info.size = len(safe)
        archive.addfile(safe_info, io.BytesIO(safe))
        leak = b"Contact private@" + b"candidate.invalid"
        leak_info = tarfile.TarInfo("jobauto/docs/leak.md")
        leak_info.size = len(leak)
        archive.addfile(leak_info, io.BytesIO(leak))

    leaks = audit_release_path(sdist)

    assert [(item.source, item.kind) for item in leaks] == [("jobauto/docs/leak.md", "email")]


def test_release_audit_ignores_local_artifacts_but_scans_public_sources(tmp_path: Path) -> None:
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "pdf-coordinates.html").write_text(
        "xMin=595." + "276000 yMin=254." + "085000",
        encoding="utf-8",
    )
    (tmp_path / ".codex_work").mkdir()
    (tmp_path / ".codex_work" / "agent-result.json").write_text(
        '{"email":"private@' + 'candidate.invalid"}',
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "public.md").write_text(
        "Contact private@" + "candidate.invalid",
        encoding="utf-8",
    )

    leaks = audit_release_path(tmp_path)

    assert [(item.source, item.kind) for item in leaks] == [("docs/public.md", "email")]
