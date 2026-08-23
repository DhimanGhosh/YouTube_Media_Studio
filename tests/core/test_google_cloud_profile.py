from __future__ import annotations

import json

import pytest

from youtube_audio_video_downloader.services.google_cloud_profile import (
    GoogleOAuthConfig,
    build_cloud_profile,
    match_portable_tracks,
    portable_playlist_snapshot,
    validate_cloud_profile,
)


def test_oauth_config_requires_an_installed_desktop_client(tmp_path) -> None:
    config_path = tmp_path / "client.json"
    config_path.write_text(
        json.dumps({"installed": {"client_id": "desktop.apps.googleusercontent.com"}}),
        encoding="utf-8",
    )

    config = GoogleOAuthConfig.from_file(config_path)

    assert config.client_id == "desktop.apps.googleusercontent.com"
    with pytest.raises(ValueError, match="desktop-client"):
        invalid = tmp_path / "invalid.json"
        invalid.write_text("{}", encoding="utf-8")
        GoogleOAuthConfig.from_file(invalid)


def test_cloud_profile_excludes_paths_and_credentials() -> None:
    profile = build_cloud_profile(
        {
            "defaults/audio_quality": "320",
            "defaults/nvidia_api_key": "secret",
            "workspace/audio_output": "C:/private/path",
            "library/volume": 42,
        },
        {"Favourites": [{"title": "Song", "artists": "Artist"}]},
        device_id="device-one",
        updated_at="2026-08-23T12:00:00Z",
    )

    assert profile["settings"] == {
        "defaults/audio_quality": "320",
        "library/volume": 42,
    }
    assert "secret" not in json.dumps(profile)
    assert "C:/private/path" not in json.dumps(profile)


def test_cloud_playlist_paths_become_identities_and_rematch_on_another_machine() -> None:
    source_items = [
        {
            "path": "C:/Music/Song.mp3",
            "title": "My Song",
            "artists": "The Artist",
            "album": "Album",
            "year": 2024,
        }
    ]
    snapshot = portable_playlist_snapshot(
        {"Favourites": ["C:/Music/Song.mp3"]}, source_items
    )
    destination_items = [
        {
            "path": "/home/user/Music/Song.flac",
            "title": "My Song",
            "artists": "The Artist",
            "album": "Album",
            "year": 2024,
        }
    ]

    paths, missing = match_portable_tracks(snapshot["Favourites"], destination_items)

    assert paths == ["/home/user/Music/Song.flac"]
    assert missing == []


def test_youtube_titles_match_local_tracks_and_report_missing() -> None:
    entries = [
        {"title": "The Artist - My Song (Official Video)", "channel": "The Artist"},
        {"title": "Unavailable Song", "channel": "Someone"},
    ]
    items = [
        {
            "path": "D:/Library/My Song.mp3",
            "title": "My Song",
            "artists": "The Artist",
        }
    ]

    paths, missing = match_portable_tracks(entries, items)

    assert paths == ["D:/Library/My Song.mp3"]
    assert missing == ["Unavailable Song"]


def test_cloud_profile_validation_drops_unknown_settings() -> None:
    profile = validate_cloud_profile(
        {
            "schema_version": 1,
            "settings": {
                "defaults/sample_rate": "48000",
                "defaults/serpapi_api_key": "must-not-restore",
            },
            "playlists": {"Cloud": [{"title": "Song"}]},
        }
    )

    assert profile["settings"] == {"defaults/sample_rate": "48000"}
