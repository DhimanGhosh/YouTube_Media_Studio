"""Tests for description timestamp and singer extraction."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from youtube_audio_video_downloader.services.youtube_track_extractor import (
    description_to_timestamp_text,
    extract_tracks_from_youtube,
    match_wikipedia_artist,
)
from youtube_audio_video_downloader.utils.track_timestamp_parser import parse_tracks_text


class YouTubeTrackExtractorTest(unittest.TestCase):
    def test_combines_track_list_with_separate_singer_credits(self) -> None:
        description = """TrackList
Ik Vaari Aa → 0:00
Raabta (Title Track) → 4:34

1. Song: Ik Vaari Aa
Singer: Arijit Singh
2. Song - Raabta Title Song
Singer- Nikhita Gandhi
"""
        self.assertEqual(
            description_to_timestamp_text(description),
            "0:00 - Ik Vaari Aa by Arijit Singh\n"
            "4:34 - Raabta (Title Track) by Nikhita Gandhi",
        )

    def test_supports_time_first_lines(self) -> None:
        description = "00:00 - Gold Tamba\nSinger: ignored without a Song credit"
        self.assertEqual(
            description_to_timestamp_text(description),
            "00:00 - Gold Tamba by ignored without a Song credit",
        )

    def test_supports_plain_whitespace_and_invisible_timestamp_separators(self) -> None:
        self.assertEqual(
            description_to_timestamp_text(
                "00:00\u200b First Song\n04:10\tSecond Song"
            ),
            "00:00 - First Song\n04:10 - Second Song",
        )

    def test_removes_track_numbers_before_titles(self) -> None:
        description = (
            "00:00 - 1 - Haanikaarak Bapu\n"
            "04:20 - 02. Dhaakad\n"
            "08:15 - #3) Gilehriyaan\n"
            "12:10 - 4: Dangal"
        )
        self.assertEqual(
            description_to_timestamp_text(description),
            "00:00 - Haanikaarak Bapu\n"
            "04:20 - Dhaakad\n"
            "08:15 - Gilehriyaan\n"
            "12:10 - Dangal",
        )

    def test_does_not_remove_a_number_that_is_part_of_the_title(self) -> None:
        self.assertEqual(
            description_to_timestamp_text("00:00 - 50-Cent Song"),
            "00:00 - 50-Cent Song",
        )

    def test_associates_singers_below_timestamp_heading(self) -> None:
        description = """Tracklist:-
Baarish - 00:00
Composed By Tanishk Bagchi
Singers - Ash King & Shashaa Tirupati
Lyricist - Arafat Mehmood

Thodi Der - 04:35
Composed By Farhan Saeed
Singers - Farhan Saeed & Shreya Ghoshal
"""
        self.assertEqual(
            description_to_timestamp_text(description),
            "00:00 - Baarish by Ash King & Shashaa Tirupati\n"
            "04:35 - Thodi Der by Farhan Saeed & Shreya Ghoshal",
        )

    def test_extracted_tracks_use_timestamp_parser_title_case(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "description": "BOL DO NA ZARA - 00:00\nSingers - Armaan Malik"
        }
        with patch("yt_dlp.YoutubeDL", return_value=downloader):
            _, tracks = extract_tracks_from_youtube("https://www.youtube.com/watch?v=example")
        title, values = next(iter(tracks[0].items()))
        self.assertEqual(title, "Bol Do Na Zara")
        self.assertEqual(values["artists"], "Armaan Malik")

    def test_uses_youtube_chapters_when_description_has_no_track_list(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Compilation",
            "duration": 600,
            "description": "No timestamp list here",
            "chapters": [
                {"start_time": 0, "title": "First Song"},
                {"start_time": 245, "title": "Second Song"},
            ],
        }
        with patch("yt_dlp.YoutubeDL", return_value=downloader):
            text, tracks = extract_tracks_from_youtube(
                "https://www.youtube.com/watch?v=example", use_ai=False
            )

        self.assertEqual(text, "00:00:00 - First Song\n00:04:05 - Second Song")
        self.assertEqual(len(tracks), 2)

    def test_deterministic_validation_accepts_non_latin_track_titles(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Bengali Compilation",
            "duration": 600,
            "description": "00:00 - বেঁচে থাকার গান\n04:10 - আমাকে আমার মত থাকতে দাও",
        }
        with patch("yt_dlp.YoutubeDL", return_value=downloader):
            _text, tracks = extract_tracks_from_youtube(
                "https://www.youtube.com/watch?v=example", use_ai=False
            )

        self.assertEqual(
            [next(iter(track)) for track in tracks],
            ["বেঁচে থাকার গান", "আমাকে আমার মত থাকতে দাও"],
        )

    def test_ai_romanizes_without_translating_non_latin_titles(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Bengali Compilation",
            "duration": 600,
            "description": "00:00 - বেঁচে থাকার গান\n04:10 - আমাকে আমার মত থাকতে দাও",
        }
        proposed = [
            {
                "start": "0:00",
                "original_title": "বেঁচে থাকার গান",
                "title": "Benche Thakar Gaan",
                "artists": "Unknown",
                "album": "Unknown",
                "release_year": "",
            },
            {
                "start": "4:10",
                "original_title": "আমাকে আমার মত থাকতে দাও",
                "title": "Amake Amar Moto Thakte Dao",
                "artists": "Unknown",
                "album": "Unknown",
                "release_year": "",
            },
        ]
        responses = [
            SimpleNamespace(data={"tracks": proposed, "reason": "romanized"}),
            SimpleNamespace(data={"accepted": True, "issues": [], "tracks": proposed}),
        ]
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.youtube_track_extractor.chat_json",
                side_effect=responses,
            ) as agent,
            patch(
                "youtube_audio_video_downloader.services.youtube_track_extractor."
                "_collect_track_search_evidence",
                return_value={},
            ),
        ):
            _text, tracks = extract_tracks_from_youtube(
                "https://www.youtube.com/watch?v=example", model="agent:test"
            )

        self.assertEqual(agent.call_count, 2)
        self.assertEqual(
            [next(iter(track)) for track in tracks],
            ["Benche Thakar Gaan", "Amake Amar Moto Thakte Dao"],
        )

    def test_non_latin_ai_failure_is_reported_instead_of_returning_source_script(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Bengali Compilation",
            "duration": 300,
            "description": "00:00 - বেঁচে থাকার গান",
        }
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.youtube_track_extractor.chat_json",
                side_effect=RuntimeError("provider unavailable"),
            ),
        ):
            with self.assertRaisesRegex(
                LookupError,
                "AI romanization failed: provider unavailable.*non-Latin titles",
            ):
                extract_tracks_from_youtube(
                    "https://www.youtube.com/watch?v=example", model="agent:test"
                )

    def test_ai_extractor_and_independent_reviewer_preserve_per_track_albums(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Mixed Jukebox",
            "duration": 600,
            "description": "00:00 First Song\n04:05 Second Song",
        }
        proposed = [
            {
                "start": "0:00", "title": "First Song", "artists": "Singer One",
                "album": "Album One", "release_year": "2018",
            },
            {
                "start": "4:05", "title": "Second Song", "artists": "Singer Two",
                "album": "Album Two", "release_year": "2020",
            },
        ]
        responses = [
            SimpleNamespace(data={"tracks": proposed, "reason": "extracted"}),
            SimpleNamespace(data={"accepted": True, "issues": [], "tracks": proposed}),
        ]
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.youtube_track_extractor.chat_json",
                side_effect=responses,
            ) as agent,
        ):
            _text, tracks = extract_tracks_from_youtube(
                "https://www.youtube.com/watch?v=example", model="agent:test"
            )

        self.assertEqual(agent.call_count, 2)
        first_values = next(iter(tracks[0].values()))
        second_values = next(iter(tracks[1].values()))
        self.assertEqual(first_values["album"], "Album One")
        self.assertEqual(second_values["album"], "Album Two")

    def test_mixed_jukebox_uses_catalog_for_missing_per_track_album(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Mixed Jukebox",
            "duration": 300,
            "description": "00:00 - First Song\nSingers - Singer One",
        }
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.youtube_track_extractor."
                "find_catalog_song_metadata",
                return_value={
                    "album": "Verified Album",
                    "year": "2019",
                    "album_art": "https://example.test/cover.jpg",
                },
            ) as catalog,
        ):
            _text, tracks = extract_tracks_from_youtube(
                "https://www.youtube.com/watch?v=example",
                use_ai=False,
                mixed_albums=True,
            )

        values = next(iter(tracks[0].values()))
        self.assertEqual(values["album"], "Verified Album")
        self.assertEqual(values["release_year"], "2019")
        catalog.assert_called_once_with("First Song", "Singer One", timeout=8)

    def test_mixed_jukebox_enriches_each_song_and_formats_catalog_artists(self) -> None:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = {
            "title": "Mixed Jukebox",
            "duration": 300,
            "description": "00:00 - First Song\n02:00 - Second Song",
        }

        def catalog_result(title: str, artists: str, timeout: int) -> dict:
            self.assertEqual(artists, "")
            self.assertEqual(timeout, 8)
            if title == "First Song":
                return {
                    "album": "First Album",
                    "artists": "Singer One & Singer Two",
                    "year": "2001",
                    "album_art": "https://example.test/first.jpg",
                }
            return {
                "album": "Second Album",
                "artists": "Singer Three and Singer Four",
                "year": "2002",
                "album_art": "https://example.test/second.jpg",
            }

        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.youtube_track_extractor."
                "find_catalog_song_metadata",
                side_effect=catalog_result,
            ),
        ):
            _text, tracks = extract_tracks_from_youtube(
                "https://www.youtube.com/watch?v=example",
                use_ai=False,
                mixed_albums=True,
            )

        first = next(iter(tracks[0].values()))
        second = next(iter(tracks[1].values()))
        self.assertEqual(first["album"], "First Album")
        self.assertEqual(first["artists"], "Singer One, Singer Two")
        self.assertEqual(first["release_year"], "2001")
        self.assertEqual(second["album"], "Second Album")
        self.assertEqual(second["artists"], "Singer Three, Singer Four")
        self.assertEqual(second["release_year"], "2002")

    def test_moves_parenthesized_singers_out_of_track_title(self) -> None:
        description = """Tere Sang Yaara (Atif Aslam) - 00:00
Rustom Vahi - Marathi (Jasraj Joshi) - 04:48
Pal Bhar (Chaahunga Reprise) - 09:00
"""
        self.assertEqual(
            description_to_timestamp_text(description),
            "00:00 - Tere Sang Yaara by Atif Aslam\n"
            "04:48 - Rustom Vahi - Marathi by Jasraj Joshi\n"
            "09:00 - Pal Bhar (Chaahunga Reprise)",
        )

    def test_supports_pointer_separator_and_removes_decorative_hearts(self) -> None:
        description = "0:00 ► DILBAR\n♥ PANIYON SA - 03:04"
        self.assertEqual(
            description_to_timestamp_text(description),
            "0:00 - DILBAR\n03:04 - PANIYON SA",
        )

    def test_keeps_parenthesized_version_when_credited_singer_differs(self) -> None:
        description = """Tu Jo Mila (Dekhna Na Mudke) - 12:08

Song: Tu Jo Mila (Dekhna Na Mudke)
Singer: Javed Ali
"""
        self.assertEqual(
            description_to_timestamp_text(description),
            "12:08 - Tu Jo Mila (Dekhna Na Mudke) by Javed Ali",
        )

    def test_remix_producer_inside_parentheses_is_not_parsed_as_artist(self) -> None:
        timestamp_text = description_to_timestamp_text(
            "0:00 - Piya (Remix by DJ Suketu)\n"
            "03:03 - Tu Mohabbat Hai (Remix by DJ Suketu)"
        )
        tracks = parse_tracks_text(timestamp_text, title_case=True)["tracks"]
        first_title, first_values = next(iter(tracks[0].items()))
        self.assertEqual(first_title, "Piya (Remix By Dj Suketu)")
        self.assertEqual(first_values["artists"], "Unknown")

    def test_wikipedia_match_tolerates_spacing_without_confusing_versions(self) -> None:
        artists = {
            "tu jo mila": "KK",
            "tu jo mila dekhna na mudke": "Javed Ali",
            "tu jo mila reprise": "Papon",
        }
        self.assertEqual(
            match_wikipedia_artist("Tu Jo Mila (Dekhna Na Mud Ke)", artists),
            "Javed Ali",
        )
        self.assertEqual(match_wikipedia_artist("Tu Jo Mila", artists), "KK")
        self.assertEqual(
            match_wikipedia_artist(
                "Shehzada Title Track", {"shehzada title track": "Sonu Nigam"}
            ),
            "Sonu Nigam",
        )


if __name__ == "__main__":
    unittest.main()
