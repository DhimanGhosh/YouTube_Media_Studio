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
    def setUp(self):
        resolver = patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_album_release", return_value={})
        self.release = resolver.start()
        self.addCleanup(resolver.stop)

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_catalog_singer_version_uses_clean_query_and_explicit_label_credits(self, youtube, wiki):
        title = "Aye Dil Pagal Mere (Sunidhi Chauhan Version)"
        wiki.return_value = [{"title": title, "artists": "Sunidhi Chauhan, Pritam", "duration_seconds": 314}]
        entry = {"id": "official", "title": "Aye Dil Pagal Mere - Naqaab | Sunidhi Chauhan | Pritam",
                 "channel_is_verified": True, "duration": 316}
        youtube.return_value.__enter__.return_value.extract_info.side_effect = [
            {"entries": [entry]}, {**entry, "description": "Singer(s): Sunidhi Chauhan\nMusic Director: Pritam"},
        ]
        result = find_individual_album_tracks("Naqaab", "2007")
        self.assertTrue(result[0][title]["ytb_link"].endswith("=official"))
        query = youtube.return_value.__enter__.return_value.extract_info.call_args_list[0].args[0]
        self.assertNotIn("(Sunidhi Chauhan Version)", query)

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_missing_duration_and_singer_version_require_verified_release_credits(self, youtube, wiki):
        title = "Ae Dil Pagal Mere (Reprise)"
        wiki.return_value = [{"title": title, "artists": "Sunidhi Chauhan", "duration_seconds": 0}]
        self.release.return_value = {"tracks": [{"title": "Aye Dil Pagal Mere (Sunidhi Chauhan Version)",
                                                "artists": "Sunidhi Chauhan, Pritam", "duration_seconds": 314}]}
        entry = {"id": "sunidhi", "title": "Aye Dil Pagal Mere", "channel": "Sunidhi Chauhan - Topic", "duration": 314}
        for singer, accepted in (("Sunidhi Chauhan", True), ("Sonu Nigam", False)):
            with self.subTest(singer=singer):
                youtube.return_value.__enter__.return_value.extract_info.side_effect = [
                    {"entries": [entry]}, {**entry, "track": entry["title"], "album": "Naqaab", "artists": [singer]},
                    {"entries": []},
                ]
                if accepted:
                    result = find_individual_album_tracks("Naqaab", "2007")
                    self.assertTrue(result[0][title]["ytb_link"].endswith("=sunidhi"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Naqaab", "2007")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata")
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_wrong_wikipedia_duration_can_use_catalog_plus_verified_description(self, youtube, wiki, catalog):
        wiki.return_value = [{"title": "Kanni Re Kanni", "artists": "Nakash Aziz", "duration_seconds": 352}]
        catalog.return_value = {"title": "Kanni Re Kanni", "album": "Goodbye", "year": "2022",
                                "duration_seconds": "232"}
        entry = {"id": "correct", "title": "Kanni Re Kanni | Goodbye | Nakash A", "duration": 242,
                 "channel_is_verified": True}
        youtube.return_value.__enter__.return_value.extract_info.side_effect = [
            {"entries": [entry]}, {"entries": []},
            {**entry, "description": "Song: Kanni Re Kanni\nSinger: Nakash Aziz"},
        ]
        result = find_individual_album_tracks("Goodbye", "2022")
        self.assertTrue(result[0]["Kanni Re Kanni"]["ytb_link"].endswith("=correct"))
        self.assertIn("Wikipedia lists 05:52", result[0]["Kanni Re Kanni"]["length_notice"])

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_missing_reprise_label_requires_explicit_description_song_and_singers(self, youtube, wiki):
        wiki.return_value = [{"title": "Maaye (Reprise)", "artists": "Devenderpal Singh, Shreya Ghoshal",
                              "duration_seconds": 274}]
        entry = {"id": "reprise", "title": "Maaye - Shreya Ghoshal | Goodbye", "duration": 287,
                 "channel_is_verified": True}
        for song, matches in (("Maaye Reprise", True), ("Maaye", False)):
            with self.subTest(song=song):
                youtube.return_value.__enter__.return_value.extract_info.side_effect = [
                    {"entries": [entry]},
                    {**entry, "description": f"Song: {song}\nSingers: Devenderpal Singh & Shreya Ghoshal"},
                    {"entries": []},
                ]
                if matches:
                    result = find_individual_album_tracks("Goodbye", "2022")
                    self.assertTrue(result[0]["Maaye (Reprise)"]["ytb_link"].endswith("=reprise"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Goodbye", "2022")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_topic_metadata_can_verify_spelling_and_album_transliteration(self, youtube, wiki):
        title = "Pyar Mein Sau Uljhanne Hai"
        wiki.return_value = [{"title": title, "artists": "Singer", "duration_seconds": 329}]
        entry = {"id": "topic", "title": "Pyaar Mein Sau Uljhane", "channel": "Singer - Topic",
                 "duration": 330}
        for album, matches in (("Kal Ho Naa Ho", True), ("Another Album", False)):
            with self.subTest(album=album):
                youtube.return_value.__enter__.return_value.extract_info.side_effect = [
                    {"entries": [entry]},
                    {**entry, "track": entry["title"], "album": album, "artists": ["Singer"]},
                    {"entries": []},
                ]
                if matches:
                    result = find_individual_album_tracks("Kaal Ho Na Ho", "2003")
                    self.assertTrue(result[0][title]["ytb_link"].endswith("=topic"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Kaal Ho Na Ho", "2003")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_catalog_title_annotation_is_cleaned_for_display_and_topic_matching(self, youtube, wiki):
        title = 'Shaajo Shaajao (From "Ballabhpurer Roopkotha") [Female]'
        wiki.return_value = [{"title": title, "artists": "Sahana Bajpaie", "duration_seconds": 273}]
        entry = {"id": "female", "title": 'Shaajo Shaajao ( From "Ballabhpurer Roopkotha") (Female)',
                 "channel": "Sahana Bajpaie - Topic", "duration": 273}
        youtube.return_value.__enter__.return_value.extract_info.side_effect = [
            {"entries": [entry]},
            {**entry, "track": entry["title"], "artists": ["Sahana Bajpaie"],
             "album": "Ballabhpurer Roopkotha (Original Motion Picture Soundtrack)"},
        ]
        result = find_individual_album_tracks("Ballabhpurer Roopkotha", "2022")
        self.assertEqual(list(result[0]), ["Shaajo Shaajao - Female"])
        self.assertTrue(result[0]["Shaajo Shaajao - Female"]["ytb_link"].endswith("=female"))

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_album_with_only_remix_versions_explains_omission_without_search(self, youtube, wiki):
        wiki.return_value = [{"title": "Song (DJ Mix)", "artists": "Singer"}]
        with self.assertRaisesRegex(LookupError, "excluding remix and DJ"):
            find_individual_album_tracks("Album")
        youtube.assert_not_called()

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata", return_value={})
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_transliterated_song_title_requires_verified_release_evidence(self, youtube, wiki, catalog):
        title = "Pyar Mein Sau Uljhanne Hai"
        wiki.return_value = [{"title": title, "duration_seconds": 329,
                              "artists": "Shankar Mahadevan, Mahalaxmi Iyer, Sneha Pant, Vijay Prakash"}]
        entry = {"id": "Bme4ck1j6B8", "duration": 330, "channel_is_verified": True,
                 "title": "Pyaar Mein Sau Uljhane | Aishwarya Rai | Vivek Oberoi | Kyun Ho Gaya Na | Shankar Mahadevan"}
        for changes, matches in (
            ({}, True),
            ({"channel_is_verified": False}, False),
            ({"duration": 180}, False),
            ({"duration": 0}, False),
            ({"title": entry["title"].replace("Kyun Ho Gaya Na", "Another Album")}, False),
            ({"title": entry["title"].replace("Shankar Mahadevan", "Another Singer")}, False),
            ({"title": entry["title"].replace("Pyaar Mein Sau Uljhane", "An Unrelated Song")}, False),
            ({"title": entry["title"].replace("Uljhane", "Uljhane Remix")}, False),
        ):
            with self.subTest(changes=changes):
                youtube.return_value.__enter__.return_value.extract_info.return_value = {
                    "entries": [{**entry, **changes}],
                }
                if matches:
                    result = find_individual_album_tracks("Kyun Ho Gaya Na", "2004")
                    self.assertTrue(result[0][title]["ytb_link"].endswith("=Bme4ck1j6B8"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Kyun Ho Gaya Na", "2004")

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
                    self.assertTrue(result[0]["I Am in Love - Male"]["ytb_link"].endswith("=recording"))
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
        self.assertTrue(result[0]["Pee Loon - Female"]["ytb_link"].endswith("=duet"))

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
                    self.assertTrue(result[0]["Pee Loon - Male"]["ytb_link"].endswith("=lyrical"))
                else:
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata", return_value={})
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_remix_and_dj_rows_are_omitted_without_searching_them(self, youtube, wiki, catalog):
        titles = ["Pee Loon", "Pee Loon (remixed by DJ A-myth)", "Pee Loon (DJ Version)",
                  "Pee Loon (DJ Star)", "Pee Loon (Mashup)"]
        wiki.return_value = [{"title": title, "artists": "Mohit Chauhan", "duration_seconds": 249}
                             for title in titles]
        youtube.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [{"id": "base", "title": "Pee Loon", "duration": 249},
                        {"id": "remix", "title": "Pee Loon (Remix)", "duration": 248}],
        }
        result = find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
        self.assertEqual([next(iter(track)) for track in result], ["Pee Loon"])
        self.assertTrue(result[0]["Pee Loon"]["ytb_link"].endswith("=base"))
        self.assertEqual(youtube.return_value.__enter__.return_value.extract_info.call_count, 2)

    def test_gender_does_not_override_conflicting_recording_variants(self):
        self.assertTrue(_variant_compatible({"female"}, {"female", "version"}))
        self.assertFalse(_variant_compatible({"male"}, {"male", "remix"}))
        self.assertFalse(_variant_compatible({"female"}, {"male"}, credited=True))
        self.assertFalse(_variant_compatible({"female"}, {"duet"}))
        self.assertFalse(_variant_compatible({"reprise"}, set(), credited=True))
        self.assertTrue(_variant_compatible({"male", "remix"}, {"remix"}, credited=True))
        self.assertFalse(_variant_compatible({"male", "remix"}, set(), credited=True))

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_gender_omission_requires_known_duration_and_surfaces_metadata_failure(self, youtube, wiki):
        extract = youtube.return_value.__enter__.return_value.extract_info
        entry = {"id": "recording", "title": "I Am in Love", "duration": 300}
        for duration in (0, 302):
            with self.subTest(duration=duration):
                wiki.return_value = [{"title": "I Am in Love (Male)", "artists": "Karthik",
                                      "duration_seconds": duration}]
                if duration:
                    extract.side_effect = [{"entries": [entry]}, DownloadError("watch-page timeout"),
                                           {"entries": []}]
                    with self.assertRaisesRegex(DownloadError, "watch-page timeout"):
                        find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
                else:
                    extract.side_effect = [{"entries": [entry]}, {"entries": []}]
                    with self.assertRaises(LookupError):
                        find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
                    self.assertEqual(extract.call_count, 2)
                extract.reset_mock()

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_gender_credit_lookups_are_bounded_per_query(self, youtube, wiki):
        wiki.return_value = [{"title": "I Am in Love (Male)", "artists": "Karthik",
                              "duration_seconds": 302}]
        extract = youtube.return_value.__enter__.return_value.extract_info
        responses = []
        for query in range(2):
            responses.append({"entries": [
                {"id": f"{query}-{index}", "title": "I Am in Love", "duration": 300}
                for index in range(8)
            ]})
            responses.extend([{"album": "Unrelated"}] * 3)
        extract.side_effect = responses
        with self.assertRaises(LookupError):
            find_individual_album_tracks("Once Upon a Time in Mumbaai", "2010")
        self.assertEqual(extract.call_count, 8)

    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_catalog_song_metadata", return_value={})
    @patch("youtube_audio_video_downloader.services.downloads.individual_track_search.find_wikipedia_tracks")
    @patch("yt_dlp.YoutubeDL")
    def test_song_named_dance_retains_title_tokens(self, youtube, wiki, catalog):
        wiki.return_value = [{"title": "Dance", "artists": "Singer", "duration_seconds": 180}]
        youtube.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [{"id": "dance", "title": "Dance Official Audio", "duration": 180}],
        }
        result = find_individual_album_tracks("Album", "2010")
        self.assertTrue(result[0]["Dance"]["ytb_link"].endswith("=dance"))

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
        self.assertIn("Pink Lips Hate Story 2 Meet Bros Anjjan, Khushboo Grewal", extract.call_args.args[0])

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
            {"title": "Kabhi Aayine (Reprise)", "artists": "KK", "duration_seconds": 285},
        ]
        extract = youtube.return_value.__enter__.return_value.extract_info
        title = "Kabhi Aayine Pe Likha Tujhe (Reprise)"
        extract.side_effect = [
            {"entries": [
                {"id": "wrong", "title": title, "channel": "Other - Topic", "duration": 285},
                {"id": "wrong-title", "title": title, "channel": "K.K. - Topic", "duration": 285},
                {"id": "right", "title": title, "channel": "K.K. - Topic", "duration": 342},
            ]},
            {"track": title, "album": "Hate Story 2", "artists": ["Other"]},
            {"track": "Another Song (Reprise)", "album": "Hate Story 2", "artists": ["K.K."]},
            {"track": title, "album": "Hate Story 2", "artists": ["K.K.", "Rashid Khan"]},
        ]

        tracks = find_individual_album_tracks("Hate Story 2", "2014")

        self.assertEqual(
            tracks[0]["Kabhi Aayine (Reprise)"]["ytb_link"], "https://www.youtube.com/watch?v=right"
        )


if __name__ == "__main__":
    unittest.main()
