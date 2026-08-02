#!/usr/bin/env python3
"""Calculate semantic versions and maintain CHANGELOG.md from Git history."""

from __future__ import annotations

import argparse
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
RELEASE_TIMEZONE = "Asia/Kolkata"
RELEASE_UTC_OFFSET = timedelta(hours=5, minutes=30)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    # Preserve Git's ASCII field/record separators (\x1f/\x1e). ``str.strip``
    # treats them as whitespace and can erase an empty commit-body field.
    return completed.stdout.rstrip("\r\n")


def project_version() -> tuple[int, int, int]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        value = str(tomllib.load(handle)["project"]["version"])
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError(f"Project version is not semantic: {value}")
    return tuple(map(int, match.groups()))


def latest_release_tag() -> tuple[str, tuple[int, int, int]] | None:
    tags = run_git("tag", "--list", "v[0-9]*", "--sort=-v:refname").splitlines()
    for tag in tags:
        match = VERSION_RE.fullmatch(tag.strip())
        if match:
            return tag.strip(), tuple(map(int, match.groups()))
    return None


def commits_since(tag: str | None) -> list[Commit]:
    revision = f"{tag}..HEAD" if tag else "HEAD"
    raw = run_git("log", revision, "--format=%H%x1f%s%x1f%b%x1e")
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        fields = record.strip("\r\n").split("\x1f", 2)
        if len(fields) == 3:
            commits.append(Commit(fields[0], fields[1].strip(), fields[2].strip()))
    return commits


def automatic_bump(commits: list[Commit]) -> str:
    if any(
        re.match(r"^[a-z]+(?:\([^)]*\))?!:", item.subject, re.IGNORECASE)
        or "BREAKING CHANGE:" in item.body.upper()
        for item in commits
    ):
        return "major"
    if any(re.match(r"^feat(?:\([^)]*\))?:", item.subject, re.IGNORECASE) for item in commits):
        return "minor"
    return "patch"


def next_version(requested_bump: str) -> tuple[str, list[Commit]]:
    latest = latest_release_tag()
    commits = commits_since(latest[0] if latest else None)
    base = max(project_version(), latest[1] if latest else (0, 0, 0))
    bump = automatic_bump(commits) if requested_bump == "auto" else requested_bump
    major, minor, patch = base
    if bump == "major":
        version = (major + 1, 0, 0)
    elif bump == "minor":
        version = (major, minor + 1, 0)
    else:
        version = (major, minor, patch + 1)
    return ".".join(map(str, version)), commits


def release_date(now: datetime | None = None) -> date:
    """Return the calendar date used in public releases for the project timezone."""
    project_timezone = timezone(RELEASE_UTC_OFFSET, RELEASE_TIMEZONE)
    current = (
        now.astimezone(project_timezone)
        if now is not None
        else datetime.now(project_timezone)
    )
    return current.date()


def changelog_section(version: str, commits: list[Commit]) -> str:
    groups: dict[str, list[Commit]] = {
        "Breaking": [],
        "Added": [],
        "Fixed": [],
        "Changed": [],
    }
    for commit in commits:
        subject = commit.subject
        if re.match(r"^[a-z]+(?:\([^)]*\))?!:", subject, re.IGNORECASE) or (
            "BREAKING CHANGE:" in commit.body.upper()
        ):
            group = "Breaking"
        elif re.match(r"^feat(?:\([^)]*\))?:", subject, re.IGNORECASE):
            group = "Added"
        elif re.match(r"^fix(?:\([^)]*\))?:", subject, re.IGNORECASE):
            group = "Fixed"
        else:
            group = "Changed"
        groups[group].append(commit)

    lines = [f"## [{version}] - {release_date().isoformat()}"]
    for heading, items in groups.items():
        if not items:
            continue
        lines.extend(["", f"### {heading}", ""])
        lines.extend(f"- {item.subject} (`{item.sha[:7]}`)" for item in items)
    if not commits:
        lines.extend(["", "### Changed", "", "- Automated release with no new commit messages."])
    return "\n".join(lines) + "\n"


def update_changelog(version: str) -> None:
    latest = latest_release_tag()
    commits = commits_since(latest[0] if latest else None)
    current = CHANGELOG.read_text(encoding="utf-8") if CHANGELOG.exists() else "# Changelog\n"
    unreleased = re.search(
        r"(?ms)^## \[Unreleased\]\s*\n(?P<body>.*?)(?=^## \[|\Z)",
        current,
    )
    if unreleased:
        body = unreleased.group("body").strip()
        section = f"## [{version}] - {release_date().isoformat()}"
        if body:
            section += f"\n\n{body}"
        remainder = current[unreleased.end() :].lstrip()
        updated = (
            current[: unreleased.start()].rstrip()
            + "\n\n"
            + section
            + ("\n\n" + remainder if remainder else "\n")
        )
    else:
        section = changelog_section(version, commits)
        first_release = current.find("\n## [")
        if first_release == -1:
            updated = current.rstrip() + "\n\n" + section
        else:
            updated = (
                current[:first_release].rstrip()
                + "\n\n"
                + section
                + current[first_release + 1 :]
            )
    CHANGELOG.write_text(updated.rstrip() + "\n", encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    next_parser = subparsers.add_parser("next-version")
    next_parser.add_argument("--bump", choices=["auto", "patch", "minor", "major"], default="auto")
    changelog_parser = subparsers.add_parser("changelog")
    changelog_parser.add_argument("--version", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "next-version":
        version, _ = next_version(args.bump)
        print(version)
    else:
        if not VERSION_RE.fullmatch(args.version):
            raise SystemExit(f"Invalid semantic version: {args.version}")
        update_changelog(args.version.removeprefix("v"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
