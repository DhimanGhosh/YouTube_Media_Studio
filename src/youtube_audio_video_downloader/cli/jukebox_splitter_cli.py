"""Command-line entry point for jukebox YouTube song extraction."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.services.jukebox_splitter import YouTubeJukeboxSplitter
from youtube_audio_video_downloader.config.settings import DownloadSettings


_DEFAULTS = DownloadSettings()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Download one jukebox/compilation YouTube video as best source audio and export "
            "manually timed high-quality MP3 songs."
        )
    )
    parser.add_argument(
        "json_file",
        type=str,
        help="Jukebox JSON/JSONC file path containing track start/end timings.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output folder. Default: jukebox_tracks/ beside the JSON file.",
    )
    parser.add_argument(
        "--preferred-mp3-quality",
        type=str,
        default=_DEFAULTS.preferred_mp3_quality,
        help=f"MP3 bitrate in kbps for exported songs. Default: {_DEFAULTS.preferred_mp3_quality}.",
    )
    parser.add_argument(
        "--audio-sample-rate",
        type=str,
        default=_DEFAULTS.audio_sample_rate,
        help=f"Output MP3 sample rate. Default: {_DEFAULTS.audio_sample_rate}.",
    )
    parser.add_argument(
        "--workers",
        "--max-workers",
        dest="workers",
        type=int,
        default=_DEFAULTS.max_workers,
        help=(
            "Number of parallel enabled jukebox source-download/split jobs. Every source "
            f"download receives an independent random delay. Default: {_DEFAULTS.max_workers}."
        ),
    )
    parser.add_argument(
        "--min-delay",
        type=int,
        default=_DEFAULTS.min_delay_seconds,
        help=(
            "Minimum random delay before each jukebox source download. "
            f"Default: {_DEFAULTS.min_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=_DEFAULTS.max_delay_seconds,
        help=(
            "Maximum random delay before each jukebox source download. "
            f"Default: {_DEFAULTS.max_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=_DEFAULTS.max_retries,
        help=f"yt-dlp retry attempts for source audio download. Default: {_DEFAULTS.max_retries}.",
    )
    parser.add_argument(
        "--overwrite",
        "--rewrite",
        action="store_true",
        help="Recreate song MP3 files even if they already exist.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the downloaded source audio under the jukebox output folder for debugging.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write jukebox_split_results.json.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    """Validate user-provided values before starting work."""

    if args.workers < 1:
        raise ValueError("--workers/--max-workers must be at least 1")
    if args.min_delay < 0 or args.max_delay < 0:
        raise ValueError("Delay values cannot be negative")
    if args.min_delay > args.max_delay:
        raise ValueError("--min-delay cannot be greater than --max-delay")
    if args.retries < 1:
        raise ValueError("--retries must be at least 1")
    if not args.preferred_mp3_quality.isdigit():
        raise ValueError("--preferred-mp3-quality must be a number such as 192, 256 or 320")


def run(args: argparse.Namespace) -> list:
    """Execute parsed jukebox splitter command and return per-song results."""

    _validate_args(args)
    settings = DownloadSettings(
        max_workers=args.workers,
        min_delay_seconds=args.min_delay,
        max_delay_seconds=args.max_delay,
        max_retries=args.retries,
        preferred_mp3_quality=args.preferred_mp3_quality,
        audio_sample_rate=args.audio_sample_rate,
        skip_existing=not args.overwrite,
    )
    splitter = YouTubeJukeboxSplitter(settings=settings)
    return splitter.split_from_json(
        args.json_file,
        output_dir=args.output_dir,
        keep_temp=args.keep_temp,
        overwrite=args.overwrite,
        write_report=not args.no_report,
    )


def _print_summary(results: list) -> None:
    """Print a consistent summary section."""

    downloaded = sum(1 for item in results if item.status.value == "downloaded")
    skipped = sum(1 for item in results if item.status.value in {"skipped", "already_exists"})
    failed = sum(1 for item in results if item.status.value == "failed")

    print("\nSummary")
    print(f"Songs exported: {downloaded}")
    print(f"Skipped/already exists: {skipped}")
    print(f"Failed: {failed}")


def main() -> int:
    """Run jukebox splitter without noisy tracebacks for expected exits."""

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
