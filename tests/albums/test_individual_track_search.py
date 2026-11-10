"""Tests for safe individual-track version matching."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.services.downloads.individual_track_search import (
    _variant_compatible,
    find_individual_album_tracks,
)


class IndividualTrackSearchTest(unittest.TestCase):
    def test_base_track_does_not_accept_remix(self) -> None:
        self.assertFalse(_variant_compatible(set(), {"remix"}))

    def test_remix_does_not_accept_base_track(self) -> None:
        self.assertFalse(_variant_compatible({"remix"}, set()))
        self.assertTrue(_variant_compatible({"remix"}, {"mix"}))

    def test_reprise_requires_reprise_recording(self) -> None:
        self.assertFalse(_variant_compatible({"reprise", "version"}, {"version"}))
        self.assertTrue(_variant_compatible({"reprise", "version"}, {"reprised"}))

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata")
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_full_audio_search_uses_verified_catalog_duration(self, youtube, wiki, catalog) -> None:
        wiki.return_value = [
            {"title": "Pink Lips", "artists": "Meet Bros Anjjan, Khushboo Grewal",
             "duration_seconds": 209},
        ]
        catalog.return_value = {
            "title": "Pink Lips", "album": "Hate Story 2", "year": "2014",
            "duration_seconds": "254.827",
        }
        extract = youtube.return_value.__enter__.return_value.extract_info
        searches = [
            {"entries": [{"id": "remix", "title": "Pink Lips Remix", "duration": 255}]},
            {"entries": [
                {"id": "cover", "title": "Pink Lips Cover", "duration": 255},
                {"id": "full", "title": "Pink Lips Full Audio Song | Hate Story 2", "duration": 255},
            ]},
        ]
        extract.side_effect = searches

        tracks = find_individual_album_tracks("Hate Story 2", "2014")

        self.assertEqual(tracks[0]["Pink Lips"]["ytb_link"], "https://www.youtube.com/watch?v=full")
        self.assertEqual(
            tracks[0]["Pink Lips"]["length_notice"],
            "Wikipedia lists 03:29. Matched YouTube audio runs 04:15.",
        )
        self.assertIn("Pink Lips Hate Story 2 full audio", extract.call_args.args[0])

        catalog.return_value["album"] = "Different album"
        extract.side_effect = searches
        unmatched = find_individual_album_tracks("Hate Story 2", "2014")[0]["Pink Lips"]
        self.assertEqual(unmatched["ytb_link"], "")
        self.assertEqual(unmatched["download"], "false")
        self.assertIn("Closest YouTube candidate runs 04:15", unmatched["length_notice"])

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_topic_recording_checks_album_and_artist_credits(self, youtube, wiki) -> None:
        wiki.return_value = [
            {"title": "Kabhi Aayine (Remix)", "artists": "KK", "duration_seconds": 285},
        ]
        extract = youtube.return_value.__enter__.return_value.extract_info
        title = "Kabhi Aayine Pe Likha Tujhe (Remix)"
        extract.side_effect = [
            {"entries": [
                {"id": "wrong", "title": title, "channel": "Other - Topic", "duration": 285},
                {"id": "right", "title": title, "channel": "K.K. - Topic", "duration": 342},
            ]},
            {"track": title, "album": "Hate Story 2", "artists": ["Other"]},
            {"track": title, "album": "Hate Story 2", "artists": ["K.K.", "Rashid Khan"]},
        ]

        tracks = find_individual_album_tracks("Hate Story 2", "2014")

        self.assertEqual(
            tracks[0]["Kabhi Aayine (Remix)"]["ytb_link"], "https://www.youtube.com/watch?v=right"
        )


if __name__ == "__main__":
    unittest.main()
