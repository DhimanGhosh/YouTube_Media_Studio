"""Command-line entry point for full-album YouTube audio splitting."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from youtube_audio_video_downloader.services.album_splitter import YouTubeAlbumSplitter
from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.config.settings import DownloadSettings


_DEFAULTS = DownloadSettings()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Download albums from either a full-album YouTube source with timestamp/silence "
            "splitting, or individual per-track YouTube links, and export tagged MP3 tracks."
        )
    )
    parser.add_argument(
        "input",
        type=str,
        help=(
            "Album JSON file path, or a direct YouTube full-album video URL. "
            "Quote URLs containing '&' in PowerShell."
        ),
    )
    parser.add_argument(
        "--album-name",
        type=str,
        default=None,
        help="Album name to use when input is a direct URL or JSON omits album/title.",
    )
    parser.add_argument(
        "--artists",
        type=str,
        default=None,
        help="Comma-separated artists to use when input is a direct URL or JSON omits artists.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output folder. Default: album_tracks/ beside the JSON file, or current directory for URL input.",
    )
    parser.add_argument(
        "--silence-threshold-db",
        type=float,
        default=-35.0,
        help="Silence detection threshold in dB. Default: -35.0. Try -30 for noisier albums.",
    )
    parser.add_argument(
        "--min-silence-duration",
        type=float,
        default=1.5,
        help="Minimum silence duration, in seconds, to consider a track boundary. Default: 1.5.",
    )
    parser.add_argument(
        "--min-track-duration",
        type=float,
        default=45.0,
        help="Ignore segments shorter than this many seconds. Default: 45.0.",
    )
    parser.add_argument(
        "--trim-silence-padding",
        type=float,
        default=0.25,
        help="Seconds to trim around each detected silence cut. Default: 0.25.",
    )
    parser.add_argument(
        "--preferred-mp3-quality",
        type=str,
        default=_DEFAULTS.preferred_mp3_quality,
        help=(
            "MP3 bitrate in kbps for exported tracks. "
            f"Default: {_DEFAULTS.preferred_mp3_quality}."
        ),
    )
    parser.add_argument(
        "--audio-sample-rate",
        type=str,
        default=_DEFAULTS.audio_sample_rate,
        help=f"Output MP3 sample rate. Default: {_DEFAULTS.audio_sample_rate}.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=_DEFAULTS.max_retries,
        help=f"yt-dlp retry attempts for source audio download. Default: {_DEFAULTS.max_retries}.",
    )
    parser.add_argument(
        "--workers",
        "--max-workers",
        type=int,
        default=_DEFAULTS.max_workers,
        help=(
            "Maximum parallel album work items. Individual song links from every enabled "
            "album share one global worker pool; full-album source jobs also run concurrently. "
            f"Default: {_DEFAULTS.max_workers}."
        ),
    )
    parser.add_argument(
        "--min-delay",
        type=int,
        default=_DEFAULTS.min_delay_seconds,
        help=(
            "Minimum random delay before each album network download. "
            f"Default: {_DEFAULTS.min_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=_DEFAULTS.max_delay_seconds,
        help=(
            "Maximum random delay before each album network download. "
            f"Default: {_DEFAULTS.max_delay_seconds}."
        ),
    )
    parser.add_argument(
        "--overwrite",
        "--rewrite",
        action="store_true",
        help="Recreate track MP3 files even if they already exist.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the downloaded source audio under the album output folder for debugging.",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Do not write album_split_results.json.",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    """Validate user-provided values before starting work."""

    if args.min_silence_duration <= 0:
        raise ValueError("--min-silence-duration must be greater than 0")
    if args.min_track_duration <= 0:
        raise ValueError("--min-track-duration must be greater than 0")
    if args.trim_silence_padding < 0:
        raise ValueError("--trim-silence-padding cannot be negative")
    if args.retries < 1:
        raise ValueError("--retries must be at least 1")
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.min_delay < 0:
        raise ValueError("--min-delay cannot be negative")
    if args.max_delay < args.min_delay:
        raise ValueError("--max-delay must be greater than or equal to --min-delay")
    if not args.preferred_mp3_quality.isdigit():
        raise ValueError("--preferred-mp3-quality must be a number such as 192, 256 or 320")


def run(args: argparse.Namespace) -> list:
    """Execute parsed album splitter command and return per-track results."""

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
    splitter = YouTubeAlbumSplitter(settings=settings)
    return splitter.split_from_input(
        args.input,
        output_dir=args.output_dir,
        album_name=args.album_name,
        artists=args.artists,
        silence_threshold_db=args.silence_threshold_db,
        min_silence_duration=args.min_silence_duration,
        min_track_duration=args.min_track_duration,
        trim_silence_padding=args.trim_silence_padding,
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
    print(f"Tracks exported: {downloaded}")
    print(f"Skipped/already exists: {skipped}")
    print(f"Failed: {failed}")


def main() -> int:
    """Run album splitter without noisy tracebacks for expected exits."""

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
