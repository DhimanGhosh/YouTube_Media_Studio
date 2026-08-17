from __future__ import annotations

import json
from pathlib import Path

from youtube_audio_video_downloader.services.ai.preference_profile import (
    load_preference_profile,
    preference_profile_path,
    remove_preference_profile,
    update_preference_profile,
)
from youtube_audio_video_downloader.services.media.media_library import LibraryItem


def item(path: Path, title: str, artists: str) -> LibraryItem:
    return LibraryItem(str(path), title, "Album", artists, 2024, 100, "audio", 1)


def test_profile_is_per_user_local_path_free_and_playlist_grounded(tmp_path: Path) -> None:
    fast = item(tmp_path / "private" / "fast.mp3", "Dance One", "Artist A")
    slow = item(tmp_path / "private" / "slow.mp3", "Quiet One", "Artist B")
    profile = update_preference_profile(
        [fast, slow],
        {"Fast Hindi": [fast.path], "Slow evenings": [slow.path]},
        data_directory=tmp_path,
    )

    assert [playlist["name"] for playlist in profile["playlists"]] == [
        "Fast Hindi",
        "Slow evenings",
    ]
    stored = preference_profile_path(tmp_path).read_text(encoding="utf-8")
    assert "private" not in stored
    assert fast.path not in stored
    assert json.loads(stored)["source"].startswith("user-created playlist")
    assert load_preference_profile(tmp_path) == profile


def test_profile_updates_when_any_users_playlists_change(tmp_path: Path) -> None:
    song = item(tmp_path / "song.mp3", "Song", "Artist")
    first = update_preference_profile(
        [song], {"Morning": [song.path]}, data_directory=tmp_path
    )
    second = update_preference_profile(
        [song], {"Workout": [song.path]}, data_directory=tmp_path
    )
    assert first["fingerprint"] != second["fingerprint"]
    assert second["playlists"][0]["name"] == "Workout"

    remove_preference_profile(tmp_path)
    assert not preference_profile_path(tmp_path).exists()
