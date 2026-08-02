from __future__ import annotations

import os
from pathlib import Path

from youtube_audio_video_downloader.config import runtime_tools


def test_bundled_tools_are_added_to_path(monkeypatch, tmp_path) -> None:
    tools = tmp_path / "runtime-tools"
    tools.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("ffmpeg", "ffprobe", "deno"):
        tool = tools / f"{name}{suffix}"
        tool.touch()
        if os.name != "nt":
            tool.chmod(0o755)
    monkeypatch.setattr(runtime_tools.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(runtime_tools.sys, "frozen", True, raising=False)
    monkeypatch.setenv("PATH", "")

    status = runtime_tools.configure_runtime_tools()

    assert status.bundled_directory == str(tools)
    assert Path(status.ffmpeg).parent == tools
    assert Path(status.ffprobe).parent == tools
    assert Path(status.deno).parent == tools
    assert status.ready


def test_missing_tools_are_reported_without_download(monkeypatch) -> None:
    monkeypatch.delenv("PATH", raising=False)
    monkeypatch.delattr(runtime_tools.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(runtime_tools.sys, "frozen", False, raising=False)

    status = runtime_tools.configure_runtime_tools(allow_download=False)

    assert not status.ready


def test_windows_subprocess_policy_hides_child_console(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class StartupInfo:
        dwFlags = 0
        wShowWindow = 1

    class FakePopen:
        __name__ = "Popen"
        __qualname__ = "Popen"

        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(runtime_tools.os, "name", "nt")
    monkeypatch.setattr(runtime_tools.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(runtime_tools.subprocess, "STARTUPINFO", StartupInfo, raising=False)
    monkeypatch.setattr(
        runtime_tools.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False
    )
    monkeypatch.setattr(
        runtime_tools.subprocess, "STARTF_USESHOWWINDOW", 0x00000001, raising=False
    )
    monkeypatch.setattr(
        runtime_tools, "_WINDOWS_SUBPROCESS_POLICY_INSTALLED", False
    )

    assert runtime_tools.configure_windows_subprocesses()
    runtime_tools.subprocess.Popen(["ffmpeg"], creationflags=4)

    assert captured["creationflags"] == 0x08000004
    startupinfo = captured["startupinfo"]
    assert startupinfo.dwFlags & 0x00000001
    assert startupinfo.wShowWindow == 0
