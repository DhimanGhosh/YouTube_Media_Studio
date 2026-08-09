from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


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


def test_empty_body_feature_commit_survives_git_record_parsing(monkeypatch) -> None:
    output = (
        "0123456789abcdef\x1ffeat(installer): add shortcut\x1f\x1e\n"
        "fedcba9876543210\x1ftest: cover shortcut\x1f\x1e\n"
    )
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(stdout=output),
    )

    commits = release.commits_since("v2.0.9")

    assert [item.subject for item in commits] == [
        "feat(installer): add shortcut",
        "test: cover shortcut",
    ]
    assert release.automatic_bump(commits) == "minor"


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


def test_release_date_uses_india_timezone_across_utc_midnight() -> None:
    github_runner_time = datetime(2026, 8, 2, 21, 53, 33, tzinfo=timezone.utc)

    assert release.release_date(github_runner_time).isoformat() == "2026-08-03"


def test_update_changelog_promotes_curated_unreleased_notes(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\nIntro.\n\n"
        "## [Unreleased]\n\n### Added\n\n- SerpApi metadata lookup.\n\n"
        "## [2.0.2] - 2026-08-02\n\n### Fixed\n\n- Previous fix.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "CHANGELOG", changelog)
    monkeypatch.setattr(release, "latest_release_tag", lambda: ("v2.0.2", (2, 0, 2)))
    monkeypatch.setattr(release, "commits_since", lambda _tag: [commit("feat: serpapi")])

    release.update_changelog("2.1.0")

    updated = changelog.read_text(encoding="utf-8")
    assert "## [Unreleased]" not in updated
    assert "## [2.1.0]" in updated
    assert "- SerpApi metadata lookup." in updated
    assert updated.index("## [2.1.0]") < updated.index("## [2.0.2]")


def test_update_changelog_promotes_unbracketed_project_heading(tmp_path, monkeypatch) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\nIntro.\n\n"
        "## Unreleased\n\n### Fixed\n\n- Correct release notes.\n\n"
        "## [2.3.0] - 2026-08-10\n\n### Added\n\n- Previous feature.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(release, "CHANGELOG", changelog)
    monkeypatch.setattr(release, "latest_release_tag", lambda: ("v2.3.0", (2, 3, 0)))
    monkeypatch.setattr(release, "commits_since", lambda _tag: [commit("fix: notes")])

    release.update_changelog("2.3.1")

    updated = changelog.read_text(encoding="utf-8")
    assert "## Unreleased" not in updated
    assert "## [2.3.1]" in updated
    assert "- Correct release notes." in updated
    assert updated.index("## [2.3.1]") < updated.index("## [2.3.0]")
