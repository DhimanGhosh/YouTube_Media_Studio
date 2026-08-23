"""Resilient yt-dlp execution, browser-cookie discovery, and diagnostics."""

from __future__ import annotations

import os
import json
import random
import shutil
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from youtube_audio_video_downloader.core.exceptions import UserCancelledError


_CLIENT_FALLBACKS = (
    ("default", "web_embedded"),
    ("web_embedded",),
    ("android_vr",),
    ("web_safari",),
    ("tv_downgraded", "web_embedded"),
)
_DIAGNOSTIC_PRINTED = False


def _emit_download_lifecycle(
    label: str, options: dict[str, Any], status: str, percent: float
) -> None:
    """Keep the Download Activity UI alive even for FFmpeg range downloads."""

    from .download_progress import DOWNLOAD_EVENT_PREFIX

    output_template = options.get("outtmpl") or ""
    if isinstance(output_template, dict):
        output_template = output_template.get("default") or ""
    payload = {
        "label": label,
        "file": Path(str(output_template)).name,
        "status": status,
        "percent": percent,
        "downloaded": 0,
        "total": 0,
        "speed": 0,
        "eta": 0,
        "connections_configured": int(
            options.get("concurrent_fragment_downloads") or 1
        ),
        "connections_used": 1,
        "fragmented": False,
        "fragment": 0,
        "fragment_count": 0,
    }
    print(DOWNLOAD_EVENT_PREFIX + json.dumps(payload), flush=True)


@dataclass(frozen=True, slots=True)
class YtDlpRuntimeDiagnostic:
    version: str
    age_days: int | None
    browser: str

    @property
    def stale(self) -> bool:
        return self.age_days is not None and self.age_days > 60


def detect_browser() -> str:
    """Return the first installed browser supported by yt-dlp cookie loading."""

    candidates: list[tuple[str, tuple[str, ...]]] = []
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        roaming = Path(os.environ.get("APPDATA", ""))
        candidates = [
            ("edge", (str(local / "Microsoft/Edge/User Data"),)),
            ("chrome", (str(local / "Google/Chrome/User Data"),)),
            ("brave", (str(local / "BraveSoftware/Brave-Browser/User Data"),)),
            ("firefox", (str(roaming / "Mozilla/Firefox/Profiles"),)),
            ("opera", (str(roaming / "Opera Software/Opera Stable"),)),
            ("vivaldi", (str(local / "Vivaldi/User Data"),)),
        ]
    elif sys.platform == "darwin":
        support = Path.home() / "Library/Application Support"
        candidates = [
            ("chrome", (str(support / "Google/Chrome"),)),
            ("edge", (str(support / "Microsoft Edge"),)),
            ("brave", (str(support / "BraveSoftware/Brave-Browser"),)),
            ("firefox", (str(support / "Firefox/Profiles"),)),
            ("opera", (str(support / "com.operasoftware.Opera"),)),
            ("vivaldi", (str(support / "Vivaldi"),)),
        ]
    else:
        config = Path.home() / ".config"
        candidates = [
            ("chrome", (str(config / "google-chrome"),)),
            ("chromium", (str(config / "chromium"),)),
            ("edge", (str(config / "microsoft-edge"),)),
            ("brave", (str(config / "BraveSoftware/Brave-Browser"),)),
            ("firefox", (str(Path.home() / ".mozilla/firefox"),)),
            ("opera", (str(config / "opera"),)),
            ("vivaldi", (str(config / "vivaldi"),)),
        ]
    for browser, paths in candidates:
        if any(Path(path).exists() for path in paths if path):
            return browser
    for browser in ("edge", "chrome", "brave", "firefox", "opera", "vivaldi"):
        if shutil.which(browser):
            return browser
    return ""


def ytdlp_runtime_diagnostic() -> YtDlpRuntimeDiagnostic:
    """Inspect the installed yt-dlp date-version and available browser cookies."""

    try:
        import yt_dlp

        version = str(yt_dlp.version.__version__)
    except (ImportError, AttributeError):
        version = "not installed"
    age_days: int | None = None
    try:
        year, month, day = (int(value) for value in version.split(".")[:3])
        age_days = max(0, (date.today() - date(year, month, day)).days)
    except (TypeError, ValueError):
        pass
    return YtDlpRuntimeDiagnostic(version, age_days, detect_browser())


def print_ytdlp_diagnostic_once() -> YtDlpRuntimeDiagnostic:
    """Expose runtime health in Live Logs once per process."""

    global _DIAGNOSTIC_PRINTED
    diagnostic = ytdlp_runtime_diagnostic()
    if not _DIAGNOSTIC_PRINTED:
        age = "unknown" if diagnostic.age_days is None else f"{diagnostic.age_days} day(s)"
        browser = diagnostic.browser or "none detected"
        print(
            f"[YT-DLP] version={diagnostic.version} | age={age} | "
            f"browser-cookies={browser}",
            flush=True,
        )
        if diagnostic.stale:
            print(
                "[YT-DLP-WARNING] This yt-dlp build is over 60 days old. "
                "Use Download diagnostics / Auto-fix or install the latest release.",
                flush=True,
            )
        _DIAGNOSTIC_PRINTED = True
    return diagnostic


def _strategy_options(
    browser: str,
    clients: tuple[str, ...],
    *,
    use_cookies: bool,
) -> dict[str, Any]:
    options: dict[str, Any] = {
        "extractor_args": {"youtube": {"player_client": list(clients)}},
    }
    if use_cookies and browser:
        options["cookiesfrombrowser"] = (browser,)
    return options


def download_with_fallback(
    url: str,
    base_options: dict[str, Any],
    *,
    label: str,
    cancellation_token=None,
    rounds: int = 1,
    download: bool = True,
) -> dict[str, Any]:
    """Download with current yt-dlp plus shuffled YouTube client/cookie fallbacks."""

    import yt_dlp

    diagnostic = print_ytdlp_diagnostic_once()
    strategies = list(_CLIENT_FALLBACKS)
    random.shuffle(strategies)
    # Latest yt-dlp defaults are the least surprising first attempt.
    strategies.insert(0, ())
    if diagnostic.browser:
        strategies.insert(1, ("default", "web_embedded"))
    last_error: Exception | None = None
    total_rounds = max(1, int(rounds))
    for round_index in range(total_rounds):
        for strategy_index, clients in enumerate(strategies):
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            use_cookies = bool(
                diagnostic.browser and strategy_index == 1 and clients
            )
            options = dict(base_options)
            if clients:
                options.update(
                    _strategy_options(
                        diagnostic.browser,
                        clients,
                        use_cookies=use_cookies,
                    )
                )
            mode = ",".join(clients) if clients else "yt-dlp default"
            cookie_text = f" + {diagnostic.browser} cookies" if use_cookies else ""
            print(
                f"[DOWNLOAD-ATTEMPT] {label} | round={round_index + 1}/{total_rounds} "
                f"| client={mode}{cookie_text}",
                flush=True,
            )
            try:
                _emit_download_lifecycle(label, options, "downloading", 0.0)
                with yt_dlp.YoutubeDL(options) as downloader:
                    result = downloader.extract_info(url, download=download)
                    if download:
                        _emit_download_lifecycle(label, options, "finished", 100.0)
                    return result if isinstance(result, dict) else {}
            except UserCancelledError:
                raise
            except Exception as exc:  # yt-dlp exposes several extractor/network errors.
                last_error = exc
                error_text = str(exc)
                is_403 = "403" in error_text or "forbidden" in error_text.lower()
                print(
                    f"[DOWNLOAD-RECOVERY] {label} | client={mode}{cookie_text} failed "
                    f"| {'YouTube HTTP 403' if is_403 else error_text}",
                    flush=True,
                )
                if strategy_index + 1 < len(strategies):
                    delay = random.randint(1, 3)
                    print(
                        f"[DOWNLOAD-RECOVERY] Trying another player client in {delay}s",
                        flush=True,
                    )
                    if cancellation_token is not None:
                        cancellation_token.wait(delay)
        if round_index + 1 < total_rounds:
            backoff = min(300, 30 * (2**round_index))
            print(
                f"[DOWNLOAD-BACKOFF] {label} | all clients failed; retrying in {backoff}s",
                flush=True,
            )
            if cancellation_token is not None:
                cancellation_token.wait(backoff)
    raise RuntimeError(
        f"All yt-dlp recovery strategies failed for {label}: {last_error}"
    )
