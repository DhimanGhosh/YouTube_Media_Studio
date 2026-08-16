from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_project_metadata_identifies_the_author_homepage_and_license() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["authors"] == [
        {"name": "Dhiman Ghosh"},
        {"email": "dgkiitcsedual@gmail.com"},
    ]
    assert project["urls"]["Homepage"] == (
        "https://github.com/DhimanGhosh/YouTube_Media_Studio"
    )
    assert project["license"] == "MIT"
    assert project["license-files"] == ["LICENSE"]


def test_repository_contains_canonical_mit_license_notice() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")

    assert license_text.startswith("MIT License\n\nCopyright (c) 2026 Dhiman Ghosh\n")
    assert "Permission is hereby granted, free of charge" in license_text
    assert 'THE SOFTWARE IS PROVIDED "AS IS"' in license_text
