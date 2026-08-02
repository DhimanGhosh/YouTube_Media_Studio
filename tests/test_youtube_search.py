"""Tests for automatic full-album YouTube search helpers."""

from __future__ import annotations

import unittest

from youtube_audio_video_downloader.services.youtube_search import (
    _description_matches_album_tracks,
    _youtube_video_identity,
    album_jukebox_query,
    rank_jukebox_candidates,
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
