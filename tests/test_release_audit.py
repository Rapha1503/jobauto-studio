import io
import tarfile
from pathlib import Path
from zipfile import ZipFile

from jobauto.release_audit import audit_release_path


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
