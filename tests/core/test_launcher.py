from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from youtube_audio_video_downloader import launcher


@pytest.fixture(autouse=True)
def configured_runtime(monkeypatch):
    status = SimpleNamespace(
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        deno="deno",
        bundled_directory="runtime-tools",
        ready=True,
    )
    monkeypatch.setattr(launcher, "configure_runtime_tools", lambda **_kwargs: status)


def test_help_does_not_start_gui(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["studio", "--help"])
    assert launcher.main() == 0
    assert "Double-click or run without arguments" in capsys.readouterr().out


def test_cli_subcommand_forwards_remaining_arguments(monkeypatch) -> None:
    received: list[str] = []

    def fake_command() -> int:
        received.extend(sys.argv)
        return 7

    monkeypatch.setitem(launcher.COMMANDS, "audio", fake_command)
    monkeypatch.setattr(sys, "argv", ["studio", "audio", "songs.json", "--overwrite"])
    assert launcher.main() == 7
    assert received == ["studio audio", "songs.json", "--overwrite"]


def test_data_directory_option_is_removed_before_cli_dispatch(monkeypatch, tmp_path) -> None:
    received: list[str] = []

    def fake_command() -> int:
        received.extend(sys.argv)
        return 0

    monkeypatch.delenv("YOUTUBE_MEDIA_STUDIO_DATA_DIR", raising=False)
    monkeypatch.setitem(launcher.COMMANDS, "audio", fake_command)
    monkeypatch.setattr(
        sys,
        "argv",
        ["studio", "--data-dir", str(tmp_path), "audio", "songs.json"],
    )

    assert launcher.main() == 0
    assert received == ["studio audio", "songs.json"]
    assert os.environ["YOUTUBE_MEDIA_STUDIO_DATA_DIR"] == str(tmp_path)


def test_data_directory_option_requires_a_path(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["studio", "--data-dir"])
    assert launcher.main() == 2
    assert "requires a folder path" in capsys.readouterr().err


def test_doctor_reports_packaged_runtime(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["studio", "doctor"])

    assert launcher.main() == 0
    output = capsys.readouterr().out
    assert "FFmpeg: ffmpeg" in output
    assert "Deno: deno" in output
    assert "yt-dlp:" in output
