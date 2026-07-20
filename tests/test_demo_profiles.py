from pathlib import Path

from jobauto.cv_source import CvSourceDocument


def test_packaged_demo_profiles_are_representative() -> None:
    profiles_root = Path(__file__).resolve().parents[1] / "config" / "profiles"

    for profile_dir in (profiles_root / "example", profiles_root / "example-b"):
        source = CvSourceDocument.parse((profile_dir / "cv_source.md").read_text(encoding="utf-8"))

        assert len(source.summary) >= 180
        assert len(source.experience) >= 2
        assert sum(len(entry.bullets) for entry in source.experience) >= 5
        assert len(source.projects) >= 3
        assert all(len(project.bullets) >= 2 for project in source.projects)
        assert len(source.skills) >= 4
        assert len(source.education) >= 2
