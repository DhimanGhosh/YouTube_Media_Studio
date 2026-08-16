"""Command-line entry point for the YouTube video downloader."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from youtube_audio_video_downloader.config.settings import DownloadSettings
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.services.downloads.video_downloader import YouTubeVideoDownloader


_DEFAULTS = DownloadSettings()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Download YouTube videos from JSON with selectable resolution. "
            "Resolution priority is JSON > CLI --resolution > settings default > highest."
        )
    )
    parser.add_argument(
        "json_file",
        type=Path,
        help="Path to the video JSON file.",
    )
    parser.add_argument(
        "--resolution",
        type=str,
        default=None,
        help=(
            "Default resolution for videos where JSON does not specify one. "
            "Examples: 8K, 4K, 2K, FHD, 1080p, HD, 720p, 480p, 360p, best, ask, mp3."
        ),
    )
    parser.add_argument(
        "--ask-quality",
        action="store_true",
        help="Ask interactively for videos that do not have resolution in JSON.",
    )
    parser.add_argument(
        "--info",
        action="store_true",
        help=(
            "Dry run: show available video qualities, estimated sizes, optional MP3 info, "
            "and the final quality/action that would be selected. This will not download anything."
        ),
    )
    parser.add_argument(
        "--mp3-mode",
        choices=("ask", "audio-only", "both"),
        default="ask",
        help=(
            "When MP3 is selected from the video command, choose whether to ask, "
            "download only MP3 audio, or download both selected video and MP3. Default: ask."
        ),
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help=(
            "Write video_download_results.json even for --info dry-run mode. "
            "By default, --info does not create this file."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Video output folder. Default: videos/ beside the JSON file.",
    )
    parser.add_argument(
        "--audio-output-dir",
        type=Path,
        default=None,
        help="MP3 output folder when MP3 is selected. Default: songs/ beside the JSON file.",
    )
    parser.add_argument(
        "--workers",
        "--max-workers",
        dest="workers",
        type=int,
        default=_DEFAULTS.max_workers,
        help=(
            "Number of parallel video/audio download workers after all quality selections "
            "are prepared. Interactive prompts are still asked sequentially to avoid mixed "
            f"console input. Default: {_DEFAULTS.max_workers}."
        ),
    )
    parser.add_argument(
        "--min-delay",
        type=int,
        default=_DEFAULTS.min_delay_seconds,
        help=(
            "Minimum random delay before each network download. "
            f"Default: {_DEFAULTS.min_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=_DEFAULTS.max_delay_seconds,
        help=(
            "Maximum random delay before each network download. "
            f"Default: {_DEFAULTS.max_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=_DEFAULTS.max_retries,
        help=f"Maximum retry attempts. Default: {_DEFAULTS.max_retries}.",
    )
    parser.add_argument(
        "--overwrite",
        "--rewrite",
        action="store_true",
        help="Delete and download again even if the target video/MP3 already exists.",
    )
    parser.add_argument(
        "--merge-format",
        default="mp4",
        choices=("mp4", "mkv", "webm"),
        help="Final merged video container format. Default: mp4.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    """Validate CLI arguments before starting yt-dlp work."""

    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.min_delay < 0 or args.max_delay < 0:
        raise ValueError("Delay values cannot be negative")
    if args.min_delay > args.max_delay:
        raise ValueError("--min-delay cannot be greater than --max-delay")
    if args.retries < 1:
        raise ValueError("--retries must be at least 1")


def run(args: argparse.Namespace) -> list:
    """Execute the parsed video command and return per-entry results."""

    _validate_args(args)
    cli_resolution = "ask" if args.ask_quality else args.resolution

    settings = DownloadSettings(
        max_workers=args.workers,
        min_delay_seconds=args.min_delay,
        max_delay_seconds=args.max_delay,
        max_retries=args.retries,
        skip_existing=not args.overwrite,
        video_merge_output_format=args.merge_format,
    )

    downloader = YouTubeVideoDownloader(settings=settings)
    return downloader.download_from_json(
        args.json_file,
        cli_resolution=cli_resolution,
        output_dir=args.output_dir,
        audio_output_dir=args.audio_output_dir,
        info_mode=args.info,
        mp3_mode=args.mp3_mode,
        write_report=args.write_report or not args.info,
    )


def _print_summary(results: list) -> None:
    """Print a consistent summary section."""

    downloaded = sum(1 for item in results if item.status.value == "downloaded")
    skipped = sum(1 for item in results if item.status.value in {"skipped", "already_exists"})
    listed = sum(1 for item in results if item.status.value == "listed")
    failed = sum(1 for item in results if item.status.value == "failed")

    print("\nSummary")
    print(f"Downloaded: {downloaded}")
    print(f"Skipped/already exists: {skipped}")
    print(f"Listed/dry-run: {listed}")
    print(f"Failed: {failed}")


def main() -> int:
    """Run video downloader from CLI arguments without noisy tracebacks."""

    try:
        results = run(build_parser().parse_args())
        _print_summary(results)
        failed = any(item.status.value == "failed" for item in results)
        return 1 if failed else 0
    except (KeyboardInterrupt, UserCancelledError):
        print("\n[CANCELLED] Operation cancelled by user. No traceback printed.")
        return 130
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
