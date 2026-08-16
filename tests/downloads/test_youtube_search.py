"""Tests for automatic full-album YouTube search helpers."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from youtube_audio_video_downloader.services.downloads.youtube_search import (
    _album_focus_score,
    _description_matches_album_tracks,
    _trusted_official_jukebox,
    _youtube_video_identity,
    album_jukebox_query,
    album_jukebox_queries,
    rank_jukebox_candidates,
    find_album_jukebox_video,
)


class YouTubeSearchTest(unittest.TestCase):
    def test_album_jukebox_query(self) -> None:
        self.assertEqual(
            album_jukebox_query("  Raabta "),
            "Raabta full album audio jukebox",
        )
        self.assertEqual(
            album_jukebox_query("Satyameva Jayate", "2018"),
            "Satyameva Jayate 2018 full album audio jukebox",
        )

    def test_album_jukebox_queries_try_natural_search_before_year_variants(self) -> None:
        queries = album_jukebox_queries("Tum Mile", "2009")

        self.assertEqual(queries[0], "Tum Mile full album audio jukebox")
        self.assertIn("Tum Mile 2009 full album audio jukebox", queries)

    def test_album_name_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "album or jukebox name"):
            album_jukebox_query("  ")

    def test_youtube_identity_matches_watch_and_short_urls(self) -> None:
        self.assertEqual(
            _youtube_video_identity("https://youtube.com/watch?v=abcdefghijk"),
            _youtube_video_identity("https://youtu.be/abcdefghijk?t=10"),
        )

    def test_ranks_verified_jukeboxes_then_views_and_rejects_single_songs(self) -> None:
        entries = [
            {"title": "Popular single", "view_count": 99_000_000, "duration": 240},
            {"title": "Album audio jukebox", "view_count": 2_000_000, "duration": 1800},
            {
                "title": "Full movie audio jukebox",
                "view_count": 8_000_000,
                "duration": 2700,
                "channel_is_verified": True,
            },
        ]
        entries[1]["title"] = "Example Album audio jukebox"
        entries[2]["title"] = "Example Album full movie audio jukebox"
        ranked = rank_jukebox_candidates(entries, album_name="Example Album")
        self.assertEqual(ranked[0]["view_count"], 8_000_000)
        self.assertEqual(len(ranked), 2)

    def test_ranks_official_audio_jukebox_over_unofficial_movie_all_songs(self) -> None:
        entries = [
            {
                "title": "Tum Mile movie all songs Emraan Hashmi movie jukebox",
                "view_count": 120_000,
                "duration": 2348,
                "channel": "N Music",
            },
            {
                "title": "Tum Mile - Audio Jukebox | Emraan Hashmi | Soha Ali Khan",
                "view_count": 489_000,
                "duration": 2743,
                "channel": "Sony Music India",
                "channel_is_verified": True,
            },
        ]

        ranked = rank_jukebox_candidates(entries, album_name="Tum Mile")

        self.assertEqual(ranked[0]["channel"], "Sony Music India")

    def test_album_focus_beats_broader_official_compilation(self) -> None:
        focused = {
            "title": "Tum Mile - Audio Jukebox | Emraan Hashmi",
        }
        compilation = {
            "title": "Best Of Pritam Part - 2 | Audio Jukebox | Jannat | Tum Mile",
        }

        self.assertGreater(
            _album_focus_score(focused, "Tum Mile"),
            _album_focus_score(compilation, "Tum Mile"),
        )

    def test_trusts_verified_audio_jukebox_when_wikipedia_rows_are_noisy(self) -> None:
        self.assertTrue(
            _trusted_official_jukebox(
                {
                    "title": "Tum Mile - Audio Jukebox | Emraan Hashmi",
                    "channel": "Sony Music India",
                    "channel_is_verified": True,
                }
            )
        )
        self.assertFalse(
            _trusted_official_jukebox(
                {
                    "title": "Tum Mile movie all songs",
                    "channel": "N Music",
                    "channel_is_verified": False,
                }
            )
        )

    @patch("youtube_audio_video_downloader.services.downloads.youtube_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_search_accepts_verified_audio_jukebox_with_youtube_chapters(
        self, ydl_class, wikipedia_mock
    ) -> None:
        flat_downloader = MagicMock()
        detail_downloader = MagicMock()
        ydl_class.side_effect = [flat_downloader, detail_downloader]
        flat_downloader.__enter__.return_value.extract_info.side_effect = [
            {
                "entries": [
                    {
                        "id": "rXIhvX4TFEA",
                        "title": "Tum Mile - Audio Jukebox | Emraan Hashmi",
                        "duration": 2743,
                        "channel": "Sony Music India",
                        "channel_is_verified": True,
                        "view_count": 489_000,
                    }
                ]
            },
            *({"entries": []} for _ in range(7)),
        ]
        detail_downloader.__enter__.return_value.extract_info.return_value = {
            "title": "Tum Mile - Audio Jukebox | Emraan Hashmi",
            "description": "No timestamp text here",
            "chapters": [
                {"start_time": 0, "title": "Tum Mile"},
                {"start_time": 343, "title": "Dil Ibaadat"},
            ],
        }
        wikipedia_mock.return_value = [{"title": "1"}, {"title": "2"}]

        result = find_album_jukebox_video("Tum Mile", "2009")

        self.assertEqual(result["url"], "https://www.youtube.com/watch?v=rXIhvX4TFEA")

    def test_rejects_popular_unrelated_jukebox(self) -> None:
        entries = [
            {
                "title": "The Prince Of Romance - Audio Jukebox",
                "view_count": 100_000_000,
                "duration": 3000,
                "channel_is_verified": True,
            },
            {
                "title": "Rustom - Full Movie Audio Jukebox",
                "view_count": 4_000_000,
                "duration": 2100,
                "channel_is_verified": True,
            },
        ]
        ranked = rank_jukebox_candidates(entries, album_name="Rustom")
        self.assertEqual(len(ranked), 1)
        self.assertIn("Rustom", ranked[0]["title"])

    def test_rejects_remix_collection_for_regular_album(self) -> None:
        entries = [{
            "title": "Tere Naal Love Ho Gaya Remix Songs Audio Jukebox",
            "view_count": 200_000,
            "duration": 1200,
            "channel_is_verified": True,
        }]
        self.assertEqual(
            rank_jukebox_candidates(entries, album_name="Tere Naal Love Ho Gaya"),
            [],
        )

    def test_rejects_description_dominated_by_album_remixes(self) -> None:
        expected = [
            {"title": "Piya O Re Piya"},
            {"title": "Jeene De"},
            {"title": "Pee Pa Pee Pa"},
            {"title": "Tu Mohabbat Hai"},
            {"title": "Fann Ban Gayi"},
            {"title": "Tu Mohabbat Hai (Remix by DJ Suketu)"},
        ]
        timestamp_text = (
            "0:00 - Piya (Remix by DJ Suketu)\n"
            "03:03 - Tu Mohabbat Hai (Remix by DJ Suketu)\n"
            "09:34 - Jeene De (Coffee House Version)"
        )
        self.assertFalse(_description_matches_album_tracks(timestamp_text, expected))

    def test_accepts_description_with_canonical_album_tracks(self) -> None:
        expected = [
            {"title": "Piya O Re Piya"},
            {"title": "Jeene De"},
            {"title": "Pee Pa Pee Pa"},
            {"title": "Tu Mohabbat Hai"},
            {"title": "Fann Ban Gayi"},
        ]
        timestamp_text = (
            "0:00 - Piya O Re Piya\n04:52 - Jeene De\n"
            "10:08 - Pee Pa Pee Pa\n12:38 - Tu Mohabbat Hai"
        )
        self.assertTrue(_description_matches_album_tracks(timestamp_text, expected))


if __name__ == "__main__":
    unittest.main()
