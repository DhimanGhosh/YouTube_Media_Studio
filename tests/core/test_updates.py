"""Tests for stable-by-default GitHub release update selection."""

from __future__ import annotations

from youtube_audio_video_downloader.services.updates import select_update


def release(tag: str, *, prerelease: bool = False) -> dict:
    return {
        "tag_name": tag,
        "name": tag,
        "body": "notes",
        "prerelease": prerelease,
        "draft": False,
        "html_url": f"https://example.test/{tag}",
        "assets": [
            {
                "name": f"YouTube-Media-Studio-{tag}-Setup.exe",
                "browser_download_url": f"https://example.test/{tag}.exe",
            },
            {
                "name": f"youtube-media-studio-{tag}-installer.run",
                "browser_download_url": f"https://example.test/{tag}.run",
            },
            {
                "name": f"youtube-media-studio-{tag}-installer.dmg",
                "browser_download_url": f"https://example.test/{tag}.dmg",
            },
            {
                "name": "SHA256SUMS.txt",
                "browser_download_url": f"https://example.test/{tag}-SHA256SUMS.txt",
            },
        ],
    }


def test_stable_channel_excludes_newer_beta(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    update = select_update(
        [release("v3.0.1b1", prerelease=True), release("v2.13.0")],
        "2.12.0",
    )
    assert update is not None
    assert update.version == "2.13.0"
    assert update.prerelease is False


def test_stable_channel_excludes_misclassified_non_prerelease_3x(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    update = select_update(
        [release("v3.1.0", prerelease=False), release("v2.13.0")],
        "2.12.0",
    )
    assert update is not None
    assert update.version == "2.13.0"


def test_beta_opt_in_selects_newest_prerelease(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    update = select_update(
        [release("v3.0.1b1", prerelease=True), release("v2.13.0")],
        "2.12.0",
        include_betas=True,
    )
    assert update is not None
    assert update.version == "3.0.1b1"
    assert update.prerelease is True


def test_signing_off_beta_returns_latest_stable(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    update = select_update(
        [release("v3.1.0b2", prerelease=True), release("v2.14.2")],
        "3.1.0b1",
        include_betas=False,
    )
    assert update is not None
    assert update.version == "2.14.2"
