"""GitHub Releases based desktop update discovery and installer download."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version


RELEASES_API = (
    "https://api.github.com/repos/DhimanGhosh/YouTube_Media_Studio/releases?per_page=30"
)


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    version: str
    tag: str
    name: str
    notes: str
    prerelease: bool
    asset_name: str
    asset_url: str
    checksum_url: str
    page_url: str


def _version(value: object) -> Version | None:
    try:
        return Version(str(value or "").strip().lstrip("vV"))
    except InvalidVersion:
        return None


def _asset_suffixes() -> tuple[str, ...]:
    system = platform.system().lower()
    if system == "windows":
        return ("-setup.exe", ".exe")
    if system == "darwin":
        return ("-installer.dmg", ".dmg")
    return ("-installer.run", ".run")


def select_update(
    releases: list[dict[str, Any]],
    current_version: str,
    *,
    include_betas: bool = False,
) -> AvailableUpdate | None:
    """Select the newest newer installable release for the chosen channel."""

    current = _version(current_version)
    if current is None:
        return None
    candidates: list[tuple[Version, dict[str, Any], dict[str, Any]]] = []
    suffixes = _asset_suffixes()
    for release in releases:
        if release.get("draft"):
            continue
        parsed = _version(release.get("tag_name"))
        prerelease = bool(release.get("prerelease")) or bool(
            parsed and parsed.is_prerelease
        )
        returning_to_stable = bool(
            not include_betas
            and current.major >= 3
            and parsed is not None
            and parsed.major == 2
            and not prerelease
        )
        if (
            parsed is None
            or (not include_betas and parsed.major != 2)
            or (parsed <= current and not returning_to_stable)
            or (prerelease and not include_betas)
        ):
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        asset = next(
            (
                value for value in assets
                if isinstance(value, dict)
                and str(value.get("name") or "").lower().endswith(suffixes)
            ),
            None,
        )
        checksum_asset = next(
            (
                value for value in assets
                if isinstance(value, dict)
                and str(value.get("name") or "").casefold() == "sha256sums.txt"
            ),
            None,
        )
        if asset is not None and checksum_asset is not None:
            asset = dict(asset)
            asset["checksum_url"] = checksum_asset.get("browser_download_url")
            candidates.append((parsed, release, asset))
    if not candidates:
        return None
    parsed, release, asset = max(candidates, key=lambda value: value[0])
    return AvailableUpdate(
        version=str(parsed),
        tag=str(release.get("tag_name") or ""),
        name=str(release.get("name") or release.get("tag_name") or parsed),
        notes=str(release.get("body") or ""),
        prerelease=bool(release.get("prerelease")) or parsed.is_prerelease,
        asset_name=str(asset.get("name") or "update"),
        asset_url=str(asset.get("browser_download_url") or ""),
        checksum_url=str(asset.get("checksum_url") or ""),
        page_url=str(release.get("html_url") or ""),
    )


def check_for_update(
    current_version: str,
    *,
    include_betas: bool = False,
    timeout: float = 8.0,
) -> AvailableUpdate | None:
    """Query public GitHub Releases. No account or Google sign-in is required."""

    request = Request(
        RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"YouTube-Media-Studio/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("GitHub Releases returned an unexpected response")
    return select_update(payload, current_version, include_betas=include_betas)


def download_update(
    update: AvailableUpdate,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download the selected installer into an isolated temporary directory."""

    if not update.asset_url:
        raise ValueError("The release does not contain a downloadable installer")
    directory = Path(tempfile.mkdtemp(prefix="youtube_media_studio_update_"))
    target = directory / Path(update.asset_name).name
    request = Request(
        update.asset_url,
        headers={"User-Agent": "YouTube-Media-Studio-Updater"},
    )
    with urlopen(request, timeout=30) as response, target.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            downloaded += len(chunk)
            if progress is not None:
                progress(downloaded, total)
    checksum_request = Request(
        update.checksum_url,
        headers={"User-Agent": "YouTube-Media-Studio-Updater"},
    )
    with urlopen(checksum_request, timeout=15) as response:
        checksum_text = response.read().decode("utf-8", errors="replace")
    expected = next(
        (
            line.split()[0].casefold()
            for line in checksum_text.splitlines()
            if len(line.split()) >= 2
            and Path(line.split()[-1].lstrip("*")).name == update.asset_name
        ),
        "",
    )
    digest = hashlib.sha256()
    with target.open("rb") as downloaded_installer:
        while chunk := downloaded_installer.read(1024 * 1024):
            digest.update(chunk)
    actual = digest.hexdigest().casefold()
    if not expected or actual != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError("Downloaded installer failed SHA-256 verification")
    if os.name != "nt" and target.suffix.casefold() == ".run":
        target.chmod(target.stat().st_mode | 0o111)
    return target
