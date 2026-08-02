"""Canonical product identity shared by the GUI, services, and packaging tools."""

from __future__ import annotations


APP_DISPLAY_NAME = "YouTube Media Studio"
ORGANIZATION_NAME = "DhimanTools"
PACKAGE_DISTRIBUTION = "youtube-media-studio"
CLI_COMMAND = "youtube-media-studio"
CLI_UNINSTALL_COMMAND = f"{CLI_COMMAND}-uninstall"
EXECUTABLE_BASENAME = "YouTubeMediaStudio"
SETTINGS_APPLICATION_NAME = "YouTubeMediaStudio"
DESKTOP_FILE_ID = "youtube-media-studio"
WINDOWS_APP_USER_MODEL_ID = f"{ORGANIZATION_NAME}.{EXECUTABLE_BASENAME}"
WINDOWS_UNINSTALL_KEY = (
    rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{EXECUTABLE_BASENAME}"
)


def http_user_agent() -> str:
    """Return a release-specific user-agent from the canonical project version."""

    from youtube_audio_video_downloader.version import application_version

    return f"{EXECUTABLE_BASENAME}/{application_version()}"
