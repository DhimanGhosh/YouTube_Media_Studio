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
    # shell=False plus a fixed argv keeps package names and interpreter paths out
    # of shell parsing. No command element is read from user or network input.
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
    completed = subprocess.run(command, check=False, shell=False)  # noqa: S603
    if completed.returncode:
        return completed.returncode
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "yt_dlp", "--version"], check=False, shell=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
