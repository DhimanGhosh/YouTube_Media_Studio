from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "prepare_release.py"
SPEC = importlib.util.spec_from_file_location("prepare_release_tool", SCRIPT)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def commit(subject: str, body: str = "") -> release.Commit:
    return release.Commit("0123456789abcdef", subject, body)


def test_automatic_semantic_version_bump() -> None:
    assert release.automatic_bump([commit("docs: clarify setup")]) == "patch"
    assert release.automatic_bump([commit("feat(gui): add queue")]) == "minor"
    assert release.automatic_bump([commit("feat!: replace job schema")]) == "major"
    assert release.automatic_bump([commit("refactor: schema", "BREAKING CHANGE: new keys")]) == "major"


def test_next_version_uses_requested_or_commit_driven_bump(monkeypatch) -> None:
    monkeypatch.setattr(release, "project_version", lambda: (2, 0, 0))
    monkeypatch.setattr(release, "latest_release_tag", lambda: ("v2.1.3", (2, 1, 3)))
    monkeypatch.setattr(release, "commits_since", lambda _tag: [commit("feat: new tool")])

    assert release.next_version("auto")[0] == "2.2.0"
    assert release.next_version("patch")[0] == "2.1.4"
    assert release.next_version("major")[0] == "3.0.0"


def test_changelog_records_subject_and_abbreviated_hash() -> None:
    section = release.changelog_section("2.1.0", [commit("feat: useful feature")])
    assert "## [2.1.0]" in section
    assert "### Added" in section
    assert "feat: useful feature (`0123456`)" in section
