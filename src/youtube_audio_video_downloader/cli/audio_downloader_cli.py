"""Command-line entry point for the downloader and MP3 metadata tagger."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from youtube_audio_video_downloader.services.downloads.audio_downloader import YouTubeAudioDownloader
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.config.settings import DownloadSettings


_DEFAULTS = DownloadSettings()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Download best available YouTube audio as MP3, or tag already-downloaded "
            "MP3 files from a JSON metadata file."
        )
    )
    parser.add_argument(
        "json_file",
        type=Path,
        help="Path to the JSON file.",
    )
    parser.add_argument(
        "--mode",
        choices=("download", "tag-existing"),
        default="download",
        help=(
            "download: read ytb_link/file_name and download MP3 files. "
            "tag-existing: read mp3_file_path and update metadata only. Default: download."
        ),
    )
    parser.add_argument(
        "--workers",
        "--max-workers",
        dest="workers",
        type=int,
        default=_DEFAULTS.max_workers,
        help=(
            "Maximum parallel audio jobs. Every network download receives its own "
            f"random delay. Default: {_DEFAULTS.max_workers}."
        ),
    )
    parser.add_argument(
        "--min-delay",
        type=int,
        default=_DEFAULTS.min_delay_seconds,
        help=(
            "Minimum random delay before each download attempt. Ignored in tag-existing "
            f"mode. Default: {_DEFAULTS.min_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--connections", type=int, default=_DEFAULTS.segment_connections,
        help="Parallel fragment connections per download (1-32; source permitting).",
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=_DEFAULTS.max_delay_seconds,
        help=(
            "Maximum random delay before each download attempt. Ignored in tag-existing "
            f"mode. Default: {_DEFAULTS.max_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=_DEFAULTS.max_retries,
        help=(
            "Maximum retry attempts per download. Ignored in tag-existing mode. "
            f"Default: {_DEFAULTS.max_retries}."
        ),
    )
    parser.add_argument(
        "--overwrite",
        "--rewrite",
        action="store_true",
        help=(
            "Delete and download again even if the target MP3 already exists. "
            "Without this option, existing MP3s are skipped and only metadata is refreshed."
        ),
    )
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="Write the JSON result report. Disabled by default.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    """Validate CLI arguments before starting download/tagging work."""

    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if not 1 <= args.connections <= 32:
        raise ValueError("--connections must be between 1 and 32")
    if args.min_delay < 0 or args.max_delay < 0:
        raise ValueError("Delay values cannot be negative")
    if args.min_delay > args.max_delay:
        raise ValueError("--min-delay cannot be greater than --max-delay")
    if args.retries < 1:
        raise ValueError("--retries must be at least 1")


def run(args: argparse.Namespace) -> list:
    """Execute parsed audio command and return per-entry results."""

    _validate_args(args)
    settings = DownloadSettings(
        max_workers=args.workers,
        segment_connections=args.connections,
        min_delay_seconds=args.min_delay,
        max_delay_seconds=args.max_delay,
        max_retries=args.retries,
        skip_existing=not args.overwrite,
    )

    downloader = YouTubeAudioDownloader(settings=settings)
    if args.mode == "tag-existing":
        return downloader.tag_existing_mp3_files_from_json(
            args.json_file, write_report=args.write_report
        )
    return downloader.download_from_json(
        args.json_file, write_report=args.write_report
    )


def _print_summary(results: list) -> None:
    """Print a consistent summary section."""

    downloaded = sum(1 for item in results if item.status.value == "downloaded")
    tagged = sum(1 for item in results if item.status.value == "tagged")
    skipped = sum(1 for item in results if item.status.value in {"skipped", "already_exists"})
    failed = sum(1 for item in results if item.status.value == "failed")

    print("\nSummary")
    if downloaded:
        print(f"Downloaded: {downloaded}")
    if tagged:
        print(f"Tagged: {tagged}")
    print(f"Skipped/already exists: {skipped}")
    print(f"Failed: {failed}")


def main() -> int:
    """Run the selected command without noisy tracebacks for expected exits."""

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
