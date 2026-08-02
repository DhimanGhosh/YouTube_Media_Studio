"""CLI for converting timestamp text into track JSON snippets."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from youtube_audio_video_downloader.utils.track_timestamp_parser import parse_tracks_to_json


def build_parser() -> argparse.ArgumentParser:
    """Create the track parser CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Parse timestamp lines such as 'Song Title - 00:00' or '00:00 - Song Title' "
            "into album/jukebox-compatible track JSON. Titles may include "
            "'by Artist Name' to populate the artists field."
        )
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        help="Optional text file containing timestamp lines. If omitted, stdin is used.",
    )
    parser.add_argument(
        "--text",
        help="Optional raw timestamp text. When supplied, this takes priority over input_file/stdin.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output JSON file. If omitted, JSON is printed to stdout.",
    )
    parser.add_argument(
        "--end-field",
        choices=("end", "stop"),
        default="end",
        help="Use 'end' for jukebox JSON or 'stop' for album JSON. Default: end.",
    )
    parser.add_argument(
        "--keep-case",
        action="store_true",
        help="Preserve title casing from input instead of applying title case.",
    )
    parser.add_argument(
        "--unknown-artists",
        default="Unknown",
        help="Artist value used when no 'by Artist' text is present. Default: Unknown.",
    )
    return parser


def _read_input(input_file: Path | None, text: str | None) -> str:
    """Read timestamp text from --text, a file, or stdin."""

    if text is not None:
        return text
    if input_file:
        return input_file.read_text(encoding="utf-8")
    return sys.stdin.read()


def main() -> int:
    """Run the track timestamp parser utility."""

    try:
        args = build_parser().parse_args()
        tracks_text = _read_input(args.input_file, args.text)
        if not tracks_text.strip():
            print("[ERROR] No track timestamp text supplied.", file=sys.stderr)
            return 2

        output_json = parse_tracks_to_json(
            tracks_text,
            end_field=args.end_field,
            title_case=not args.keep_case,
            unknown_artists=args.unknown_artists,
        )

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output_json + "\n", encoding="utf-8")
            print(f"[SAVED] Track JSON saved to: {args.output}")
        else:
            print(output_json)

        return 0
    except KeyboardInterrupt:
        print("\n[CANCELLED] Operation cancelled by user. No traceback printed.")
        return 130
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
