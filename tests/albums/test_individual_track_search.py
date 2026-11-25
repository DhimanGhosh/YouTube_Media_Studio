"""Tests for safe individual-track version matching."""

from __future__ import annotations

import unittest
from unittest.mock import patch
from yt_dlp.utils import DownloadError

from youtube_audio_video_downloader.services.downloads.individual_track_search import (
    _variant_compatible,
    find_individual_album_tracks,
)


class IndividualTrackSearchTest(unittest.TestCase):
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_gender_label_can_be_omitted_only_with_matching_release_credits(self, youtube, wiki):
        wiki.return_value = [{"title": "I Am in Love (Male)", "artists": "Karthik",
                              "duration_seconds": 302}]
        extract = youtube.return_value.__enter__.return_value.extract_info
        for changes, matches in (
            ({}, True),
            ({"artists": ["KK", "Dominique Cerejo"]}, False),
            ({"album": "Another Album"}, False),
            ({"track": "Another Song"}, False),
            ({"track": "I Am in Love (Female)"}, False),
            ({"track": "I Am in Love (Remix)"}, False),
            ({"duration": 190}, False),
            ({"artists": []}, False),
        ):
            with self.subTest(changes=changes):
                entry = {"id": "recording", "title": "I Am in Love", "duration": 300,
                         "channel": "Pritam"}
                metadata = {**entry, "track": "I Am in Love",
                            "album": "Once Upon a Time in Mumbaai", "artists": ["Pritam", "Karthik"],
                            **changes}
                extract.side_effect = [{"entries": [entry]}, metadata, {"entries": []}]
                if matches:
                    result = find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
                    self.assertTrue(result[0]["I Am in Love (Male)"]["ytb_link"].endswith("=recording"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_female_catalog_label_can_match_credited_duet_upload(self, youtube, wiki):
        wiki.return_value = [{"title": "Pee Loon (Female)", "artists": "Tulsi Kumar",
                              "duration_seconds": 105}]
        extract = youtube.return_value.__enter__.return_value.extract_info
        extract.side_effect = [
            {"entries": [{"id": "duet", "title": "Pee Loon (Duet)", "duration": 105}]},
            {"id": "duet", "track": "Pee Loon (Duet)", "duration": 105,
             "album": "Once Upon a Time in Mumbaai", "artists": ["Pritam", "Tulsi Kumar"]},
        ]
        result = find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
        self.assertTrue(result[0]["Pee Loon (Female)"]["ytb_link"].endswith("=duet"))

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_verified_lyrical_upload_uses_labeled_description_credits(self, youtube, wiki):
        wiki.return_value = [{"title": "Pee Loon (Male)", "artists": "Mohit Chauhan",
                              "duration_seconds": 288}]
        extract = youtube.return_value.__enter__.return_value.extract_info
        for verified, singer, matches in ((True, "Mohit Chauhan", True),
                                          (False, "Mohit Chauhan", False),
                                          (True, "Tulsi Kumar", False)):
            with self.subTest(verified=verified, singer=singer):
                entry = {"id": "lyrical", "title": '"Pee Loon" Lyrical Song',
                         "duration": 306, "channel_is_verified": verified}
                extract.side_effect = [
                    {"entries": [entry]},
                    {**entry, "description": f"Singer: {singer}\nMovie: Once Upon a Time in Mumbaai"},
                    {"entries": []},
                ]
                if matches:
                    result = find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
                    self.assertTrue(result[0]["Pee Loon (Male)"]["ytb_link"].endswith("=lyrical"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata", return_value={})
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_remix_attribution_is_not_required_in_youtube_title(self, youtube, wiki, catalog):
        title = "Pee Loon (remixed by DJ A-myth)"
        wiki.return_value = [{"title": title, "artists": "Mohit Chauhan", "duration_seconds": 249}]
        youtube.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [{"id": "base", "title": "Pee Loon", "duration": 249},
                        {"id": "remix", "title": "Pee Loon (Remix)", "duration": 248}],
        }
        result = find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
        self.assertTrue(result[0][title]["ytb_link"].endswith("=remix"))

    def test_gender_does_not_override_conflicting_recording_variants(self):
        self.assertTrue(_variant_compatible({"female"}, {"female", "version"}))
        self.assertFalse(_variant_compatible({"male"}, {"male", "remix"}))
        self.assertFalse(_variant_compatible({"female"}, {"male"}, credited=True))
        self.assertFalse(_variant_compatible({"female"}, {"duet"}))
        self.assertFalse(_variant_compatible({"reprise"}, set(), credited=True))

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata", return_value={})
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_checks_original_title_for_translated_official_audio_search_result(
        self, youtube, wiki, song_metadata,
    ) -> None:
        wiki.return_value = [{"title": "Zakhmi", "artists": "Rito Riba", "duration_seconds": 191}]
        extract = youtube.return_value.__enter__.return_value.extract_info
        for original_title, expected in (
            ("Zakhmi (Official Audio) Dilfek Rito Riba", "https://www.youtube.com/watch?v=zakhmi12345"),
            ("Unrelated (Official Audio) Dilfek Rito Riba", ""),
        ):
            with self.subTest(original_title=original_title):
                extract.side_effect = [
                    {"entries": [{"id": "zakhmi12345", "title": "Wounded (Official Audio) Dilfek Rito Riba",
                                  "channel_is_verified": True, "duration": 189}]},
                    {"id": "zakhmi12345", "title": original_title, "duration": 189},
                    {"entries": []},
                ]
                if expected:
                    tracks = find_individual_album_tracks("Dilfek", "2026")
                    self.assertEqual(tracks[0]["Zakhmi"]["ytb_link"], expected)
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Dilfek", "2026")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata", return_value={})
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_album_tracks")
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_catalog_fallback_reaches_youtube_when_wikipedia_is_missing(
        self, youtube, wiki, catalog, song_metadata,
    ) -> None:
        catalog.return_value = [{"title": "Makhmali", "artists": "Rito Riba, Saaheal",
                                 "duration_seconds": 180, "source": "Apple Music catalog"}]
        for failure in ([], OSError("Wikipedia unavailable")):
            with self.subTest(failure=failure):
                wiki.side_effect = failure if isinstance(failure, Exception) else None
                wiki.return_value = []
                youtube.return_value.__enter__.return_value.extract_info.return_value = {
                    "entries": [{"id": "makhmali123", "title": "Makhmali Dilfek Rito Riba Saaheal",
                                 "duration": 180}]
                }
                tracks = find_individual_album_tracks("Dilfek", "2026")
                self.assertEqual(tracks[0]["Makhmali"]["ytb_link"],
                                 "https://www.youtube.com/watch?v=makhmali123")
                self.assertEqual(catalog.call_args.args, ("Dilfek", "2026"))

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
            {"entries": [
                {"id": "remix", "title": "Pink Lips Remix", "duration": 255},
                {"id": "short", "title": "Pink Lips | Hate Story 2", "duration": 209},
            ]},
            {"entries": [
                {"id": "cover", "title": "Pink Lips Cover", "duration": 255},
                {"id": "wrong", "title": "Pink Lips | Hate Story 2 | Other Artist", "duration": 255},
                {"id": "full", "title": "Pink Lips | Hate Story 2 | Meet Bros Anjjan, Khushboo Grewal",
                 "duration": 255},
            ]},
        ]
        extract.side_effect = searches

        tracks = find_individual_album_tracks("Hate Story 2", "2014")

        self.assertEqual(tracks[0]["Pink Lips"]["ytb_link"], "https://www.youtube.com/watch?v=full")
        wiki.assert_called_once_with("Hate Story 2", "2014", preferred_languages_only=True)
        self.assertEqual(
            tracks[0]["Pink Lips"]["length_notice"],
            "Wikipedia lists 03:29. Matched YouTube audio runs 04:15.",
        )
        self.assertIn("Pink Lips Hate Story 2 full audio", extract.call_args.args[0])

        extract.side_effect = [{"entries": [searches[1]["entries"][-1]]}, DownloadError("timeout")]
        tracks = find_individual_album_tracks("Hate Story 2", "2014")
        self.assertTrue(tracks[0]["Pink Lips"]["ytb_link"].endswith("=full"))

        extract.side_effect = [{"entries": [searches[0]["entries"][-1]]}, DownloadError("timeout")]
        catalog.side_effect = ValueError("invalid JSON")
        tracks = find_individual_album_tracks("Hate Story 2", "2014")
        self.assertTrue(tracks[0]["Pink Lips"]["ytb_link"].endswith("=short"))

        catalog.side_effect = None
        catalog.return_value["album"] = "Different album"
        extract.side_effect = [{"entries": []}, searches[1]]
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
                {"id": "wrong-title", "title": title, "channel": "K.K. - Topic", "duration": 285},
                {"id": "right", "title": title, "channel": "K.K. - Topic", "duration": 342},
            ]},
            {"track": title, "album": "Hate Story 2", "artists": ["Other"]},
            {"track": "Another Song (Remix)", "album": "Hate Story 2", "artists": ["K.K."]},
            {"track": title, "album": "Hate Story 2", "artists": ["K.K.", "Rashid Khan"]},
        ]

        tracks = find_individual_album_tracks("Hate Story 2", "2014")

        self.assertEqual(
            tracks[0]["Kabhi Aayine (Remix)"]["ytb_link"], "https://www.youtube.com/watch?v=right"
        )


if __name__ == "__main__":
    unittest.main()
