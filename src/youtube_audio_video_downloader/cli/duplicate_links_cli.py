"""CLI for finding duplicate YouTube links in JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from youtube_audio_video_downloader.utils.duplicate_links import find_duplicate_youtube_links


def build_parser() -> argparse.ArgumentParser:
    """Create the duplicate-link CLI parser."""

    parser = argparse.ArgumentParser(
        description="Find duplicate YouTube links in songs/videos/albums/jukebox JSON files."
    )
    parser.add_argument("json_file", type=Path, help="Path to the JSON file to inspect.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path. If omitted, only terminal output is printed.",
    )
    return parser


def run(args: argparse.Namespace) -> list[dict]:
    """Execute duplicate-link detection."""

    duplicates = find_duplicate_youtube_links(args.json_file)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(duplicates, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )

    return duplicates


def _print_report(duplicates: list[dict], output: Path | None = None) -> None:
    """Print a readable duplicate-link report."""

    if not duplicates:
        print("No duplicate YouTube links found.")
        return

    print("\nDuplicate YouTube links found:\n")
    for index, duplicate in enumerate(duplicates, start=1):
        print(f"Group {index}")
        print(f"Link : {duplicate['ytb_link']}")
        print(f"Count: {duplicate['count']}")
        print("Entries:")
        for entry_name in duplicate["entries"]:
            print(f"  - {entry_name}")
        print()

    if output:
        print(f"[SAVED] Duplicate report saved to: {output}")


def main() -> int:
    """Run the duplicate-link utility."""

    try:
        args = build_parser().parse_args()
        duplicates = run(args)
        _print_report(duplicates, args.output)
        return 1 if duplicates else 0
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
