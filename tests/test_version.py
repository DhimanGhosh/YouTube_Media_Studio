from __future__ import annotations

import tomllib
from unittest.mock import patch

from youtube_audio_video_downloader import version as version_module
from youtube_audio_video_downloader.config.app_identity import (
    EXECUTABLE_BASENAME,
    http_user_agent,
)


def test_runtime_version_matches_project_version() -> None:
    with open("pyproject.toml", "rb") as handle:
        expected = str(tomllib.load(handle)["project"]["version"])

    version_module.application_version.cache_clear()
    assert version_module.application_version() == expected


def test_installed_distribution_metadata_is_authoritative() -> None:
    version_module.application_version.cache_clear()
    with patch.object(version_module.metadata, "version", return_value="9.8.7"):
        assert version_module.application_version() == "9.8.7"
    version_module.application_version.cache_clear()


def test_network_user_agent_uses_the_same_runtime_version() -> None:
    assert http_user_agent() == (
        f"{EXECUTABLE_BASENAME}/{version_module.application_version()}"
    )
