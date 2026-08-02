"""Unified frozen-app launcher: GUI by default, CLI when given a subcommand."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from youtube_audio_video_downloader.config.runtime_tools import (
    configure_runtime_tools,
    configure_windows_subprocesses,
)

from youtube_audio_video_downloader.cli.album_splitter_cli import main as album_main
from youtube_audio_video_downloader.cli.artist_formatter_cli import main as artists_main
from youtube_audio_video_downloader.cli.audio_downloader_cli import main as audio_main
from youtube_audio_video_downloader.cli.duplicate_links_cli import main as duplicates_main
from youtube_audio_video_downloader.cli.jukebox_splitter_cli import main as jukebox_main
from youtube_audio_video_downloader.cli.track_parser_cli import main as timestamps_main
from youtube_audio_video_downloader.cli.video_downloader_cli import main as video_main

# Service modules import yt-dlp only when a job starts, so installing this policy
# here still precedes every external media process while keeping imports conventional.
configure_windows_subprocesses()

Command = Callable[[], int]

COMMANDS: dict[str, Command] = {
    "audio": audio_main,
    "video": video_main,
    "album": album_main,
    "jukebox": jukebox_main,
    "duplicates": duplicates_main,
    "artists": artists_main,
    "timestamps": timestamps_main,
}

HELP = """YouTube Media Studio

Double-click or run without arguments to open the GUI.

CLI usage:
  YouTubeMediaStudio [--data-dir FOLDER] <command> [command options]

Global options:
  --data-dir FOLDER  Use FOLDER for persistent application data for this run

Commands:
  audio       Download/tag MP3 files
  video       Inspect or download video/audio
  album       Split a full album
  jukebox     Split a jukebox/compilation
  duplicates  Find duplicate YouTube links
  artists     Normalize artist names
  timestamps  Convert timestamps to track JSON
  doctor      Verify bundled FFmpeg, FFprobe, and Deno

Use `YouTubeMediaStudio <command> --help` for command-specific help.
"""


def _prime_asyncio_runtime() -> None:
    """Initialize asyncio on the main thread before downloader workers start."""

    import asyncio

    # Python 3.14's asyncio package exposes this module while its package
    # initializer runs. Touching it here prevents parallel frozen workers from
    # observing a partially initialized package during their first yt-dlp import.
    if not hasattr(asyncio, "base_events"):
        raise RuntimeError("The packaged asyncio runtime is incomplete")


def _attach_windows_console() -> None:
    """Attach a windowed frozen executable to its invoking Windows console."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        ctypes.windll.kernel32.AttachConsole(-1)
        if sys.stdout is None:
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        if sys.stderr is None:
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        if sys.stdin is None:
            sys.stdin = open("CONIN$", "r", encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    _prime_asyncio_runtime()
    args = sys.argv[1:]
    try:
        args, data_directory = _extract_data_directory(args)
    except ValueError as exc:
        _attach_windows_console()
        print(str(exc), file=sys.stderr)
        return 2
    if data_directory:
        os.environ["YOUTUBE_MEDIA_STUDIO_DATA_DIR"] = data_directory
    runtime_status = configure_runtime_tools(allow_download=not getattr(sys, "frozen", False))
    if not args or args[0] in {"gui", "--gui"}:
        if args:
            sys.argv = [sys.argv[0], *args[1:]]
        from youtube_audio_video_downloader.gui.app import main as gui_main

        return gui_main()

    _attach_windows_console()
    if args[0] in {"help", "--help", "-h"}:
        print(HELP)
        return 0
    if args[0].casefold() == "doctor":
        import yt_dlp

        print(f"FFmpeg: {runtime_status.ffmpeg or 'MISSING'}")
        print(f"FFprobe: {runtime_status.ffprobe or 'MISSING'}")
        print(f"Deno: {runtime_status.deno or 'MISSING'}")
        print(f"yt-dlp: {yt_dlp.version.__version__}")
        print(f"Bundled tools: {runtime_status.bundled_directory or 'not a frozen package'}")
        return 0 if runtime_status.ready else 1
    command = COMMANDS.get(args[0].casefold())
    if command is None:
        print(f"Unknown command: {args[0]}\n\n{HELP}", file=sys.stderr)
        return 2
    sys.argv = [f"{sys.argv[0]} {args[0]}", *args[1:]]
    return command()


def _extract_data_directory(args: list[str]) -> tuple[list[str], str]:
    """Remove the launcher-level data-directory option before CLI dispatch."""

    remaining: list[str] = []
    selected = ""
    index = 0
    while index < len(args):
        argument = args[index]
        if argument == "--data-dir":
            if index + 1 >= len(args) or not args[index + 1].strip():
                raise ValueError("--data-dir requires a folder path")
            selected = args[index + 1]
            index += 2
            continue
        if argument.startswith("--data-dir="):
            selected = argument.partition("=")[2].strip()
            if not selected:
                raise ValueError("--data-dir requires a folder path")
            index += 1
            continue
        remaining.append(argument)
        index += 1
    return remaining, selected
