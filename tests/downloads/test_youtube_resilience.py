"""Tests for yt-dlp runtime diagnostics and recovery orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.downloads.youtube_resilience import (
    detect_browser,
    download_with_fallback,
    ytdlp_runtime_diagnostic,
)


def test_detect_browser_finds_windows_edge_profile(tmp_path, monkeypatch) -> None:
    edge = tmp_path / "Microsoft" / "Edge" / "User Data"
    edge.mkdir(parents=True)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert detect_browser() == "edge"


def test_runtime_diagnostic_reports_date_version() -> None:
    diagnostic = ytdlp_runtime_diagnostic()
    assert diagnostic.version.count(".") >= 2
    assert diagnostic.age_days is not None


def test_download_falls_back_after_403(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeDownloader:
        def __init__(self, options):
            calls.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _url, *, download):
            if len(calls) == 1:
                raise RuntimeError("HTTP Error 403: Forbidden")
            return {"_filename": str(Path("ok.webm")), "download": download}

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeDownloader)
    with (
        patch(
            "youtube_audio_video_downloader.services.downloads.youtube_resilience.detect_browser",
            return_value="",
        ),
        patch("random.randint", return_value=0),
    ):
        result = download_with_fallback(
            "https://example.test/video", {"outtmpl": "file.%(ext)s"}, label="Test"
        )
    assert result["download"] is True
    assert len(calls) == 2

