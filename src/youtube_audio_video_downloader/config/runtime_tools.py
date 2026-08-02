"""Discover media executables shipped inside a frozen desktop package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeToolStatus:
    ffmpeg: str
    ffprobe: str
    deno: str
    bundled_directory: str

    @property
    def ready(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe and self.deno)


_WINDOWS_SUBPROCESS_POLICY_INSTALLED = False


def configure_windows_subprocesses() -> bool:
    """Prevent command-line media tools from creating focus-stealing windows.

    A PyInstaller ``--windowed`` executable has no parent console.  Windows then
    creates a new console for every FFmpeg/FFprobe/Deno child unless the creation
    flags explicitly suppress it.  Installing the policy before importing the
    downloader services also covers subprocesses started internally by yt-dlp.
    """

    global _WINDOWS_SUBPROCESS_POLICY_INSTALLED
    if os.name != "nt" or _WINDOWS_SUBPROCESS_POLICY_INSTALLED:
        return False

    original_popen = subprocess.Popen
    create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    startf_use_showwindow = int(
        getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
    )
    sw_hide = 0

    class HiddenWindowsPopen(original_popen):
        def __init__(self, *args, **kwargs) -> None:
            kwargs["creationflags"] = (
                int(kwargs.get("creationflags", 0) or 0) | create_no_window
            )
            startupinfo = kwargs.get("startupinfo")
            if startupinfo is None:
                startupinfo = subprocess.STARTUPINFO()
                kwargs["startupinfo"] = startupinfo
            startupinfo.dwFlags |= startf_use_showwindow
            startupinfo.wShowWindow = sw_hide
            super().__init__(*args, **kwargs)

    HiddenWindowsPopen.__name__ = original_popen.__name__
    HiddenWindowsPopen.__qualname__ = original_popen.__qualname__
    setattr(subprocess, "Popen", HiddenWindowsPopen)
    _WINDOWS_SUBPROCESS_POLICY_INSTALLED = True
    return True


def configure_runtime_tools(*, allow_download: bool = False) -> RuntimeToolStatus:
    """Put packaged tools on PATH, optionally fetching FFmpeg for source installs."""

    bundled = bundled_tools_directory()
    if bundled is not None:
        _prepend_path(bundled)

    if allow_download and (not shutil.which("ffmpeg") or not shutil.which("ffprobe")):
        try:
            from portable_ffmpeg import FFmpegVersions, get_ffmpeg

            ffmpeg, _ffprobe = get_ffmpeg(FFmpegVersions.V7)
            _prepend_path(ffmpeg.parent)
        except (ImportError, OSError, RuntimeError, ValueError):
            pass

    if not shutil.which("deno"):
        try:
            import deno

            deno_binary = Path(deno.find_deno_bin())
            if deno_binary.is_file():
                _prepend_path(deno_binary.parent)
        except (ImportError, OSError):
            pass

    return RuntimeToolStatus(
        ffmpeg=shutil.which("ffmpeg") or "",
        ffprobe=shutil.which("ffprobe") or "",
        deno=shutil.which("deno") or "",
        bundled_directory=str(bundled or ""),
    )


def bundled_tools_directory() -> Path | None:
    """Return PyInstaller's runtime-tools folder when all expected tools exist."""

    executable_suffix = ".exe" if os.name == "nt" else ""
    names = [f"ffmpeg{executable_suffix}", f"ffprobe{executable_suffix}", f"deno{executable_suffix}"]
    roots: list[Path] = []
    bundle_root = getattr(sys, "_MEIPASS", "")
    if bundle_root:
        roots.append(Path(bundle_root))
    if getattr(sys, "frozen", False):
        roots.extend([Path(sys.executable).resolve().parent, Path(sys.executable).resolve().parent / "_internal"])
    for root in roots:
        candidate = root / "runtime-tools"
        if all((candidate / name).is_file() for name in names):
            return candidate
    return None


def _prepend_path(directory: Path) -> None:
    value = str(directory.resolve())
    current = os.environ.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    if value not in entries:
        os.environ["PATH"] = os.pathsep.join([value, *entries])
