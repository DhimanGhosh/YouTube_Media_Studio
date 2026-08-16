"""Library-wide artist identity repair behavior."""

from __future__ import annotations

from unittest.mock import patch

from youtube_audio_video_downloader.services.media.artist_canonicalizer import (
    apply_artist_replacements,
    repair_artist_metadata,
    suggest_artist_renames,
)
from youtube_audio_video_downloader.services.media.media_metadata import EditableMediaMetadata


def test_suggestions_merge_dotted_initials_and_short_full_name_variants() -> None:
    suggestions = suggest_artist_renames(
        [
            "K.K, Arijit",
            "K.K., Arijit Singh",
            "KK, Abhijeet",
            "A. R. Rahman",
            "A R Rahman, Abhijeet Bhattacharya",
        ]
    )

    assert {(item.detected, item.replacement) for item in suggestions} == {
        ("K.K", "KK"),
        ("K.K.", "KK"),
        ("Arijit", "Arijit Singh"),
        ("Abhijeet", "Abhijeet Bhattacharya"),
        ("A. R. Rahman", "AR Rahman"),
        ("A R Rahman", "AR Rahman"),
    }


def test_reviewed_replacements_remove_duplicate_credits() -> None:
    assert apply_artist_replacements(
        "K.K., KK, Arijit, Arijit Singh",
        {"K.K.": "KK", "Arijit": "Arijit Singh"},
    ) == "KK, Arijit Singh"


@patch(
    "youtube_audio_video_downloader.services.media.artist_canonicalizer.replace_media_metadata"
)
@patch(
    "youtube_audio_video_downloader.services.media.artist_canonicalizer.read_media_metadata"
)
def test_repair_updates_artist_and_album_artist_tags(read_mock, replace_mock) -> None:
    read_mock.return_value = EditableMediaMetadata(
        artists="K.K., Arijit", album_artist="A. R. Rahman"
    )

    report = repair_artist_metadata(
        ["song.mp3"],
        {"K.K.": "KK", "Arijit": "Arijit Singh", "A. R. Rahman": "AR Rahman"},
    )

    assert len(report.updated) == 1
    replace_mock.assert_called_once_with(
        report.updated[0],
        {"artists": "KK, Arijit Singh", "album_artist": "AR Rahman"},
    )
