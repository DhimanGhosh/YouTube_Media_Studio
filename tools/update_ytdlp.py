#!/usr/bin/env python3
"""Update yt-dlp for a source/development installation and verify the result."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "yt-dlp[default,deno]",
    ]
    print("Updating yt-dlp with:", " ".join(command), flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        return completed.returncode
    return subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--version"], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
