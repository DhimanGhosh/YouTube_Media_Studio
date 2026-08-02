"""CLI for normalizing artist-name strings."""

from __future__ import annotations

import argparse
import sys

from youtube_audio_video_downloader.utils.artist_name_formatter import format_artist_names


def build_parser() -> argparse.ArgumentParser:
    """Create the artist formatter parser."""

    parser = argparse.ArgumentParser(
        description="Format artist names into a clean comma-separated string."
    )
    parser.add_argument(
        "artists",
        nargs="*",
        help="Artist text. If omitted, the command reads one value from stdin.",
    )
    return parser


def main() -> int:
    """Run the artist formatter utility."""

    try:
        args = build_parser().parse_args()
        raw_text = " ".join(args.artists).strip()
        if not raw_text:
            raw_text = sys.stdin.read().strip()

        if not raw_text:
            print("[ERROR] No artist text supplied.", file=sys.stderr)
            return 2

        print(format_artist_names(raw_text))
        return 0
    except KeyboardInterrupt:
        print("\n[CANCELLED] Operation cancelled by user. No traceback printed.")
        return 130
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
