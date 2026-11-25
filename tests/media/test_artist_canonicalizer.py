"""Library-wide artist identity repair behavior."""

from __future__ import annotations

from unittest.mock import patch

from youtube_audio_video_downloader.services.media.artist_canonicalizer import (
    apply_artist_replacements,
    count_artist_replacement_matches,
    repair_artist_metadata,
    suggest_artist_renames,
)
from youtube_audio_video_downloader.services.media.media_metadata import EditableMediaMetadata


def test_screenshot_capitalization_variants_share_one_canonical_artist() -> None:
    values = [
        "Yo Yo Honey Singh, Mamta Sharma, Nitu Choudhry",
        "YO YO Honey Singh",
        "Pritam, Imran Aziz Mian, Yo Yo Honey Singh, Irshad Kamil",
        "KK, Shefali Alvares, Yo Yo Honey Singh",
        "Mika Singh, Bohemia, Yo Yo Honey Singh, Shefali Alvares",
    ]
    suggestions = suggest_artist_renames(values)
    replacements = {item.detected: item.replacement for item in suggestions}
    assert replacements["YO YO Honey Singh"] == "Yo Yo Honey Singh"
    fixed = [apply_artist_replacements(value, replacements) for value in values]
    assert fixed[1] == "Yo Yo Honey Singh"
    assert not suggest_artist_renames(fixed)


def test_case_variants_of_unlisted_artists_use_one_existing_spelling() -> None:
    values = ["DJ Example", "DJ Example", "Dj Example", "will.i.am", "Will.I.Am"]
    suggestions = suggest_artist_renames(values)
    replacements = {item.detected: item.replacement for item in suggestions}
    assert replacements == {"Dj Example": "DJ Example", "Will.I.Am": "will.i.am"}
    fixed = [apply_artist_replacements(value, replacements) for value in values]
    assert fixed == ["DJ Example", "DJ Example", "DJ Example", "will.i.am", "will.i.am"]
    assert not suggest_artist_renames(fixed)
    assert suggest_artist_renames(reversed(values)) == suggestions


def test_case_duplicates_do_not_make_a_longer_artist_name_ambiguous() -> None:
    suggestions = suggest_artist_renames(
        ["Example Artist", "Example Artist Senior", "EXAMPLE Artist Senior"]
    )
    assert {(item.detected, item.replacement) for item in suggestions} == {
        ("Example Artist", "Example Artist Senior"),
        ("EXAMPLE Artist Senior", "Example Artist Senior"),
    }


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


def test_contextual_duo_suggestion_does_not_replace_ambiguous_solo_name() -> None:
    suggestions = suggest_artist_renames(
        ["Vishal, Shekhar", "Vishal", "Vishal Mishra", "Unknown"]
    )

    assert ("Vishal, Shekhar", "Vishal Dadlani, Shekhar Ravjiani") in {
        (item.detected, item.replacement) for item in suggestions
    }
    assert not any(item.detected in {"Vishal", "Unknown"} for item in suggestions)


def test_manual_whole_credit_rule_matches_separators_but_not_solo_artist() -> None:
    replacements = {"Vishal, Shekhar": "Vishal Dadlani, Shekhar Ravjiani"}

    assert apply_artist_replacements("Vishal & Shekhar", replacements) == (
        "Vishal Dadlani, Shekhar Ravjiani"
    )
    assert apply_artist_replacements("Vishal", replacements) == "Vishal"
    assert count_artist_replacement_matches(
        ["Vishal, Shekhar", "Vishal & Shekhar", "Vishal Mishra", "Vishal"],
        "Vishal, Shekhar",
    ) == 2


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


@patch("youtube_audio_video_downloader.services.media.artist_canonicalizer.replace_media_metadata")
@patch("youtube_audio_video_downloader.services.media.artist_canonicalizer.read_media_metadata")
def test_case_repair_updates_both_tags_and_removes_duplicate_credit(read_mock, replace_mock):
    read_mock.return_value = EditableMediaMetadata(
        artists="YO YO Honey Singh, Yo Yo Honey Singh, KK", album_artist="YO YO Honey Singh",
    )
    replacements = {item.detected: item.replacement for item in suggest_artist_renames(
        [read_mock.return_value.artists]
    )}
    assert replacements["YO YO Honey Singh"] == "Yo Yo Honey Singh"
    report = repair_artist_metadata(["song.mp3"], replacements)
    assert not report.failed
    replace_mock.assert_called_once_with(
        report.updated[0], {"artists": "Yo Yo Honey Singh, KK", "album_artist": "Yo Yo Honey Singh"},
    )


def test_reviewed_case_variants_can_have_distinct_exact_replacements():
    assert apply_artist_replacements("DJ Example, Dj Example", {
        "DJ Example": "First Artist", "Dj Example": "Second Artist",
    }) == "First Artist, Second Artist"


def test_equal_usage_preserves_acronyms_and_unknown_placeholders():
    suggestions = suggest_artist_renames(["DJ Example", "Dj Example", "Unknown artist"])
    assert [(item.detected, item.replacement) for item in suggestions] == [("Dj Example", "DJ Example")]
    assert apply_artist_replacements("Unknown artist", {"Dj Example": "DJ Example"}) == "Unknown artist"
