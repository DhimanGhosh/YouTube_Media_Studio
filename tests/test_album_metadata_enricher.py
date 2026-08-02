"""Tests for parallel, evidence-backed folder metadata enrichment."""

from __future__ import annotations

import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.album_metadata_enricher import (
    MetadataEnrichmentReport,
    _album_year_consensus,
    _album_track_match,
    _apply_mixed_language_qualifiers,
    _catalog_recording_matches_file,
    _conflicting_album_year_paths,
    _normalize_display_title,
    _rename_enriched_audio,
    _title_track_album_hint,
    _verified_conflicting_album_years,
    enrich_folder_metadata,
)
from youtube_audio_video_downloader.services.media_metadata import EditableMediaMetadata
from youtube_audio_video_downloader.services.metadata_verifier import (
    MetadataVerificationDecision,
)


class AlbumMetadataEnricherTest(unittest.TestCase):
    def setUp(self) -> None:
        targets = (
            "find_wikipedia_song_metadata",
            "find_wikipedia_tracks",
            "find_catalog_song_metadata",
            "find_album_release_year",
        )
        self.lookup_patchers = [
            patch(
                "youtube_audio_video_downloader.services.album_metadata_enricher."
                + target,
                return_value=[] if target == "find_wikipedia_tracks" else {},
            )
            for target in targets
        ]
        for patcher in self.lookup_patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cleans_stray_quotes_before_a_version_label(self) -> None:
        self.assertEqual(
            _normalize_display_title('Bhoy Dekhas Na”, (male version)'),
            "Bhoy Dekhas Na (Male Version)",
        )

    def test_extracts_album_only_from_explicit_title_track_label(self) -> None:
        self.assertEqual(_title_track_album_hint("Le Chakka Title Track"), "Le Chakka")
        self.assertEqual(_title_track_album_hint("Prem Amar (Title Song)"), "Prem Amar")
        self.assertEqual(_title_track_album_hint("An Ordinary Song"), "")

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_art",
        return_value="https://example.test/lorai.jpg",
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher."
        "find_serpapi_song_metadata",
        return_value={
            "title": "Jonaki",
            "album": "Lorai",
            "artists": "Papon",
            "year": "2014",
            "source": "SerpApi Google Search",
        },
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher."
        "serpapi_is_configured",
        return_value=True,
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_serpapi_fills_missing_album_year_and_artwork(
        self, read_mock, replace_mock, _configured_mock, serp_mock, _art_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata()
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "Jonaki - Papon.mp3"
            original.write_bytes(b"media")

            report = enrich_folder_metadata(
                directory, workers=1, ai_enabled=False
            )

            renamed = Path(directory) / "Jonaki - Lorai (2014) - Papon.mp3"
            self.assertEqual(report.updated, (renamed,))
            serp_mock.assert_called_once_with("Jonaki", "Papon")
            replace_mock.assert_called_once_with(
                original,
                {
                    "title": "Jonaki",
                    "album": "Lorai (2014)",
                    "artists": "Papon",
                    "year": "2014",
                },
                artwork_path="https://example.test/lorai.jpg",
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_mixed_language_album_tracks_receive_language_qualifiers(
        self, read_mock, replace_mock
    ) -> None:
        read_mock.side_effect = [
            EditableMediaMetadata(
                title="Hindi Song", album="Amanush (1975)",
                artists="Singer One", year="1975",
            ),
            EditableMediaMetadata(
                title="Bengali Song", album="Amanush (1975)",
                artists="Singer Two", year="1975",
            ),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hindi = root / "Hindi Song - Amanush (1975) - Singer One.mp3"
            bengali = root / "Bengali Song - Amanush (1975) - Singer Two.mp3"
            hindi.write_bytes(b"hindi")
            bengali.write_bytes(b"bengali")

            changed = _apply_mixed_language_qualifiers(
                {hindi: "Hindi", bengali: "Bengali"}, _retries=1
            )

            self.assertEqual(len(changed), 2)
            self.assertTrue(all(path.exists() for path in changed.values()))
            albums = {call.args[1]["album"] for call in replace_mock.call_args_list}
            self.assertEqual(
                albums, {"Amanush (Hindi) (1975)", "Amanush (Bengali) (1975)"}
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_release_year",
        return_value={"year": "2010", "page_title": "Le Chakka"},
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_verified_title_track_supplies_missing_album(
        self, read_mock, _year_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Le Chakka Title Track",
            album="",
            artists="Kunal Ganjawala",
            year="",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / "Le Chakka Title Track - Kunal Ganjawala.mp3"
            track.write_bytes(b"media")

            report = enrich_folder_metadata(directory, workers=1)

            renamed = Path(directory) / (
                "Le Chakka Title Track - Le Chakka (2010) - Kunal Ganjawala.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            replace_mock.assert_called_once_with(
                track, {"album": "Le Chakka (2010)", "year": "2010"},
                artwork_path=None,
            )

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher."
        "verify_metadata_evidence",
        return_value=MetadataVerificationDecision(
            "apply",
            {
                "title": "Banke Tera Jogi",
                "album": "Phir Bhi Dil Hai Hindustani",
                "artists": "Sonu Nigam, Alka Yagnik, Lalit Pandit",
                "year": "2000",
            },
            "",
            0.95,
            "Verified Wikipedia album-table identity",
            ("wikipedia",),
        ),
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher._media_duration",
        return_value=278.0,
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher."
        "find_wikipedia_tracks",
        return_value=[
            {
                "title": "Banke Tera Jogi",
                "artists": "Sonu Nigam, Alka Yagnik, Lalit Pandit",
                "duration_seconds": 283,
            }
        ],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_unknown_artist_placeholder_is_searched_and_replaced(
        self, read_mock, tracks_mock, replace_mock, _duration_mock, verify_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Banke Tere Jodi",
            album="Phir Bhi Dil Hai Hindustani (2000)",
            artists="Unknown",
            year="2000",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / (
                "Banke Tere Jodi - Phir Bhi Dil Hai Hindustani (2000) - Unknown.mp3"
            )
            original.write_bytes(b"media")

            report = enrich_folder_metadata(
                directory, workers=1, agentic_model="configured-model"
            )

            renamed = Path(directory) / (
                "Banke Tera Jogi - Phir Bhi Dil Hai Hindustani (2000) - "
                "Sonu Nigam, Alka Yagnik, Lalit Pandit.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            self.assertTrue(renamed.exists())
            tracks_mock.assert_called_once_with("Phir Bhi Dil Hai Hindustani", "2000")
            self.assertEqual(
                verify_mock.call_args.args[0],
                {
                    "title": "Banke Tera Jogi",
                    "album": "Phir Bhi Dil Hai Hindustani",
                    "artists": "",
                    "year": "2000",
                },
            )
            self.assertEqual(
                verify_mock.call_args.args[1]["artists"],
                "Sonu Nigam, Alka Yagnik, Lalit Pandit",
            )
            self.assertEqual(verify_mock.call_args.args[2], {})
            replace_mock.assert_called_once_with(
                original,
                {
                    "title": "Banke Tera Jogi",
                    "artists": "Sonu Nigam, Alka Yagnik, Lalit Pandit",
                },
                artwork_path=None,
            )

    def test_album_track_match_normalizes_gender_version_titles(self) -> None:
        tracks = [
            {"title": "I'm the Best (Male Version)", "artists": "Abhijeet"},
            {"title": "I'm the Best (Female Version)", "artists": "Jaspinder Narula"},
        ]

        self.assertEqual(
            _album_track_match(tracks, "I Am The Best(Male)", Path("male.mp3")),
            tracks[0],
        )
        self.assertEqual(
            _album_track_match(tracks, "I Am The Best(Female)", Path("female.mp3")),
            tracks[1],
        )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher."
        "find_wikipedia_tracks",
        return_value=[
            {
                "title": "Banke Tera Jogi",
                "artists": "Sonu Nigam, Alka Yagnik, Lalit Pandit",
                "duration_seconds": 283,
            }
        ],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_force_recheck_replaces_catalog_credit_with_album_table_singers(
        self, read_mock, _tracks_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Banke Tera Jogi",
            album="Phir Bhi Dil Hai Hindustani (2000)",
            artists="Jatin-Lalit, Alka Yagnik, Sonu Nigam",
            year="2000",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / (
                "Banke Tera Jogi - Phir Bhi Dil Hai Hindustani (2000) - "
                "Jatin-Lalit, Alka Yagnik, Sonu Nigam.mp3"
            )
            original.write_bytes(b"media")

            report = enrich_folder_metadata(
                directory, workers=1, force_recheck=True
            )

            renamed = Path(directory) / (
                "Banke Tera Jogi - Phir Bhi Dil Hai Hindustani (2000) - "
                "Sonu Nigam, Alka Yagnik, Lalit Pandit.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            replace_mock.assert_called_once_with(
                original,
                {"artists": "Sonu Nigam, Alka Yagnik, Lalit Pandit"},
                artwork_path=None,
            )

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher._media_duration",
        return_value=246.0,
    )
    def test_conflicting_catalog_album_requires_matching_duration(
        self, _duration_mock
    ) -> None:
        self.assertFalse(
            _catalog_recording_matches_file(
                Path("Khela Sesh.mp3"), {"duration_seconds": "292.067"}
            )
        )
        self.assertTrue(
            _catalog_recording_matches_file(
                Path("Khela Sesh.mp3"), {"duration_seconds": "247.5"}
            )
        )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_unanimous_sibling_album_year_is_used_as_consensus(self, read_mock) -> None:
        read_mock.side_effect = [
            EditableMediaMetadata(album="Hawa Bodol", year=""),
            EditableMediaMetadata(album="Hawa Bodol (2013)", year="2013"),
            EditableMediaMetadata(album="Hawa Bodol (2013)", year="2013"),
        ]

        result = _album_year_consensus(
            [Path("one.mp3"), Path("two.mp3"), Path("three.mp3")]
        )

        self.assertEqual(result, {"hawa bodol": "2013"})

    def test_conflicting_sibling_album_years_are_forced_out_of_tracker(self) -> None:
        parent = Path("library") / "Hero (2006)"
        first = parent / "One - Hero (2021) - Singer.mp3"
        second = parent / "Two - Hero (2006) - Singer.mp3"

        self.assertEqual(
            _conflicting_album_year_paths([first, second]), {first, second}
        )

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_release_year",
        return_value={"year": "1999", "page_title": "Aa Ab Laut Chalen"},
    )
    def test_conflicting_sibling_years_use_one_verified_album_year(self, _year_mock) -> None:
        parent = Path("library") / "Aa Ab Laut Chalen (1999)"
        paths = [
            parent / "One - Aa Ab Laut Chalen (1998) - Singer.mp3",
            parent / "Two - Aa Ab Laut Chalen (1999) - Singer.mp3",
        ]

        self.assertEqual(
            _verified_conflicting_album_years(paths),
            {"aa ab laut chalen": "1999"},
        )

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher._media_quality_score",
        side_effect=[(128000, 0, 100), (320000, 1, 200)],
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher._media_duration",
        side_effect=[240.0, 241.0],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_enricher_removes_a_lower_quality_duplicate_recording(
        self, read_mock, _duration_mock, _quality_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song", album="Album (2001)", artists="Artist", year="2001"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "Incoming.mp3"
            existing = root / "Song - Album (2001) - Artist.mp3"
            candidate.write_bytes(b"candidate")
            existing.write_bytes(b"existing")

            destination = _rename_enriched_audio(
                candidate, "Song", "Album (2001)", "Artist"
            )

            self.assertEqual(destination, existing)
            self.assertFalse(candidate.exists())
            self.assertEqual(existing.read_bytes(), b"existing")

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher._media_duration",
        side_effect=[240.0, 420.0],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_enricher_preserves_same_named_tracks_with_different_durations(
        self, read_mock, _duration_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song", album="Album (2001)", artists="Artist", year="2001"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / "Incoming.mp3"
            existing = root / "Song - Album (2001) - Artist.mp3"
            candidate.write_bytes(b"candidate")
            existing.write_bytes(b"existing")

            destination = _rename_enriched_audio(
                candidate, "Song", "Album (2001)", "Artist"
            )

            self.assertEqual(destination, root / "Song - Album (2001) - Artist (2).mp3")
            self.assertTrue(destination.exists())
            self.assertTrue(existing.exists())

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.reorder_track_numbers",
        return_value=1,
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_rectifies_implausible_single_song_track_number(
        self, read_mock, _replace_mock, reorder_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Jab Talak",
            album="Cocktail 2",
            artists="Arijit Singh, Akasa Singh",
            year="2025",
            track_number="63",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            original = source / "Jab Talak - Cocktail 2 - Arijit Singh, Akasa Singh.mp3"
            song = source / "Jab Talak - Cocktail 2 (2025) - Arijit Singh, Akasa Singh.mp3"
            tracker = source / ".tracker" / "completed.json"
            original.write_bytes(b"media")

            report = enrich_folder_metadata(source, workers=1, tracker_path=tracker)

            reorder_mock.assert_called_once_with(
                [song], retries=3, normalize_total=True
            )
            self.assertEqual(report.completed, (song,))
            self.assertTrue(tracker.is_file())

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_normalizes_legacy_artist_separator_in_tag_and_filename(
        self, read_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Tenu Na Bol Pawaan",
            album="Behen Hogi Teri",
            artists="Jyotica Tangri/Yasser Desai",
            year="2017",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            original = source / (
                "Tenu Na Bol Pawaan - Behen Hogi Teri - "
                "Jyotica Tangri_Yasser Desai.mp3"
            )
            original.write_bytes(b"media")

            report = enrich_folder_metadata(source, workers=1)

            renamed = source / (
                "Tenu Na Bol Pawaan - Behen Hogi Teri (2017) - "
                "Jyotica Tangri, Yasser Desai.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            self.assertTrue(renamed.exists())
            replace_mock.assert_called_once_with(
                original,
                {
                    "album": "Behen Hogi Teri (2017)",
                    "artists": "Jyotica Tangri, Yasser Desai",
                },
                artwork_path=None,
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_art",
        return_value="",
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.verify_metadata_evidence",
        return_value=MetadataVerificationDecision(
            "review",
            {},
            "",
            0.75,
            "Wikipedia and catalog evidence are missing.",
            (),
            (),
        ),
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_year_qualified_folder_repairs_title_track_without_internet_evidence(
        self, read_mock, _verify_mock, _album_art_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Jab Koi Baat",
            album="Jab Koi Baat",
            artists="Atif Aslam/Shirley Setia",
            year="2018",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            album_folder = Path(directory) / "Jab Koi Baat (2018)"
            album_folder.mkdir()
            original = album_folder / (
                "Jab Koi Baat - Jab Koi Baat (2018) - "
                "Atif Aslam_Shirley Setia.mp3"
            )
            original.write_bytes(b"media")

            report = enrich_folder_metadata(
                directory,
                workers=1,
                agentic_model="configured-model",
                force_recheck=True,
            )

            renamed = album_folder / (
                "Jab Koi Baat - Jab Koi Baat (2018) - "
                "Atif Aslam, Shirley Setia.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            self.assertTrue(renamed.exists())
            replace_mock.assert_called_once_with(
                original,
                {
                    "album": "Jab Koi Baat (2018)",
                    "artists": "Atif Aslam, Shirley Setia",
                },
                artwork_path=None,
            )

    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher."
        "MAX_PARALLEL_WORKERS",
        12,
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher._enrich_one_file")
    def test_more_than_nine_workers_execute_concurrently(self, enrich_one_mock) -> None:
        barrier = threading.Barrier(12, timeout=30)
        state_lock = threading.Lock()
        active = 0
        maximum_active = 0

        def work(path, _token):
            nonlocal active, maximum_active
            with state_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                barrier.wait()
            finally:
                with state_lock:
                    active -= 1
            return "skipped", "test complete", path

        enrich_one_mock.side_effect = work
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            for index in range(12):
                (source / f"Song {index}.mp3").write_bytes(b"media")

            report = enrich_folder_metadata(source, workers=12)

        self.assertEqual(report.scanned, 12)
        self.assertEqual(maximum_active, 12)

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_art",
        return_value="https://example.com/cover.jpg",
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_release_year",
        return_value={"year": "2020", "page_title": "Album"},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_tracks",
        return_value=[{"title": "Song", "artists": "Exact Singer"}],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_exact_wikipedia_match_fills_only_missing_fields(
        self, read_mock, _tracks_mock, _year_mock, _art_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(title="Song", album="Album")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            track = source / "Song - Album - Exact Singer.mp3"
            track.write_bytes(b"media")
            report = enrich_folder_metadata(source, workers=4)

            renamed = source / "Song - Album (2020) - Exact Singer.mp3"
            self.assertEqual(report.updated, (renamed,))
            replace_mock.assert_called_once_with(
                track,
                {
                    "album": "Album (2020)",
                    "artists": "Exact Singer",
                    "year": "2020",
                },
                artwork_path="https://example.com/cover.jpg",
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_release_year",
        side_effect=LookupError("not found"),
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_tracks",
        return_value=[],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_no_verified_match_does_not_edit_the_file(
        self, read_mock, _tracks_mock, _year_mock, _catalog_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(title="Song", album="Unverified")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "Song.mp3").write_bytes(b"media")
            report = enrich_folder_metadata(source, workers=2)
            self.assertEqual(report.updated, ())
            self.assertEqual(len(report.skipped), 1)
            replace_mock.assert_not_called()

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={
            "title": "Chine Phelechhi Rastaghat",
            "album": "Aschhe Bachhor Abar Hobe",
            "artists": "Arijit Singh & Anweshaa",
            "year": "2015",
            "album_art": "https://example.com/album.jpg",
        },
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_song_metadata",
        return_value={},
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_title_artist_filename_uses_verified_catalog_fallback(
        self, read_mock, _wikipedia_mock, catalog_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            track = source / "Chine Phelechhi Rastaghat - Arijit Singh, Anweshaa.mp3"
            track.write_bytes(b"media")
            report = enrich_folder_metadata(source, workers=3)
            renamed = source / (
                "Chine Phelechhi Rastaghat - Aschhe Bachhor Abar Hobe (2015) - "
                "Arijit Singh, Anweshaa.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            self.assertTrue(renamed.exists())
            catalog_mock.assert_called_once_with(
                "Chine Phelechhi Rastaghat", "Arijit Singh, Anweshaa"
            )
            replace_mock.assert_called_once_with(
                track,
                {
                    "title": "Chine Phelechhi Rastaghat",
                    "album": "Aschhe Bachhor Abar Hobe (2015)",
                    "artists": "Arijit Singh, Anweshaa",
                    "year": "2015",
                },
                artwork_path="https://example.com/album.jpg",
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_song_metadata",
        return_value={
            "title": "Song",
            "album": "Arijit Singh Hits",
            "artists": "Arijit Singh",
            "year": "2020",
        },
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_rejects_artist_as_album_but_keeps_independently_verified_year(
        self, read_mock, _wiki_mock, _catalog_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata()
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / "Song - Arijit Singh.mp3"
            track.write_bytes(b"media")
            report = enrich_folder_metadata(directory, workers=1)

            self.assertEqual(report.updated, (track,))
            replace_mock.assert_called_once_with(
                track,
                {"title": "Song", "artists": "Arijit Singh", "year": "2020"},
                artwork_path=None,
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_song_metadata",
        return_value={
            "title": "Song",
            "album": "Verified Film",
            "artists": "Arijit Singh",
            "year": "2020",
        },
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_repairs_artist_contaminated_album_even_when_other_fields_are_complete(
        self, read_mock, _wiki_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song",
            album="Arijit Singh",
            artists="Arijit Singh",
            year="2020",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / "Song - Arijit Singh.mp3"
            track.write_bytes(b"media")
            report = enrich_folder_metadata(directory, workers=1)

            renamed = Path(directory) / "Song - Verified Film (2020) - Arijit Singh.mp3"
            self.assertEqual(report.updated, (renamed,))
            self.assertTrue(renamed.exists())
            replace_mock.assert_called_once_with(
                track, {"album": "Verified Film (2020)"}, artwork_path=None
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_complete_audio_is_renamed_recursively_and_video_is_not_scanned(
        self, read_mock, _replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Nested Song",
            album="Nested Album",
            artists="Artist One, Artist Two",
            year="2022",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            nested = source / "one" / "two"
            nested.mkdir(parents=True)
            original = nested / "old name.mp3"
            original.write_bytes(b"audio")
            video = nested / "movie.mp4"
            video.write_bytes(b"video")

            report = enrich_folder_metadata(source, workers=4)

            renamed = nested / (
                "Nested Song - Nested Album (2022) - Artist One, Artist Two.mp3"
            )
            self.assertEqual(report.scanned, 1)
            self.assertEqual(report.updated, (renamed,))
            self.assertTrue(renamed.exists())
            self.assertTrue(video.exists())
            self.assertEqual(read_mock.call_args_list[0].args, (original,))
            self.assertEqual(read_mock.call_args_list[1].args, (renamed,))

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_release_year",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_tracks",
        return_value=[],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_structured_filename_retags_missing_core_fields_without_inventing_extras(
        self, read_mock, _wiki_mock, _catalog_mock, _year_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata()
        with tempfile.TemporaryDirectory() as directory:
            nested = Path(directory) / "nested"
            nested.mkdir()
            track = nested / "Real Song - Real Album - Artist One, Artist Two.mp3"
            track.write_bytes(b"audio")

            report = enrich_folder_metadata(directory, workers=2)

            self.assertEqual(report.updated, (track,))
            replace_mock.assert_called_once_with(
                track,
                {
                    "title": "Real Song",
                    "album": "Real Album",
                    "artists": "Artist One, Artist Two",
                },
                artwork_path=None,
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_song_metadata",
        return_value={},
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_removes_artist_contaminated_album_when_no_replacement_is_verified(
        self, read_mock, _wiki_mock, _catalog_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song",
            album="Best of Arijit Singh",
            artists="Arijit Singh",
            year="2020",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / "Song.mp3"
            track.write_bytes(b"media")
            output = StringIO()
            with redirect_stdout(output):
                report = enrich_folder_metadata(directory, workers=1)

            self.assertEqual(report.updated, (track,))
            self.assertIn(
                "[ENRICHED] Song.mp3: Album metadata field removed",
                output.getvalue(),
            )
            replace_mock.assert_called_once_with(
                track, {"album": ""}, artwork_path=None
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_release_year",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={},
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_tracks",
        return_value=[],
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    @patch("youtube_audio_video_downloader.services.album_folders.read_media_metadata")
    def test_repairs_soundtrack_suffix_in_source_and_destination_trees(
        self, folder_read_mock, read_mock, _wiki_mock, _catalog_mock, _year_mock,
        replace_mock,
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Song",
            album="Byabodhan (Original Motion Picture Soundtrack) - EP",
            artists="Artist",
            year="2025",
            artwork_present=True,
        )
        folder_read_mock.return_value = read_mock.return_value
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            dirty_destination = (
                root
                / "destination"
                / "Byabodhan (Original Motion Picture Soundtrack) - EP"
            )
            destination = root / "destination" / "Byabodhan (2025)"
            source.mkdir()
            dirty_destination.mkdir(parents=True)
            source_file = source / "source old.mp3"
            destination_file = dirty_destination / "destination old.mp3"
            source_file.write_bytes(b"audio")
            destination_file.write_bytes(b"audio")

            report = enrich_folder_metadata(
                source, additional_folders=(root / "destination",), workers=2
            )

            renamed = {
                source / "Song - Byabodhan (2025) - Artist.mp3",
                destination / "Song - Byabodhan (2025) - Artist.mp3",
            }
            self.assertEqual(report.scanned, 2)
            self.assertEqual(set(report.updated), renamed)
            self.assertTrue(all(path.exists() for path in renamed))
            self.assertFalse(dirty_destination.exists())
            self.assertEqual(replace_mock.call_count, 2)
            for call in replace_mock.call_args_list:
                self.assertEqual(call.args[1], {"album": "Byabodhan (2025)"})

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={
            "title": "Bhalo Lage Swapnoke",
            "album": "Hero",
            "artists": "Shreya Ghoshal & Sonu Nigam",
            "year": "2021",
            "album_art": "https://covers.example/hero.jpg",
        },
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_song_metadata",
        return_value={
            "title": "Bhalo Lage Swapnoke",
            "album": "Hero",
            "artists": "Sonu Nigam, Shreya Ghoshal",
            "year": "2006",
            "page_title": "Sonu Nigam discography",
        },
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_preserves_existing_album_and_year_while_refreshing_artwork(
        self, read_mock, _wiki_mock, _catalog_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Bhalo Lage Swapnoke",
            album="Hero (1983)",
            artists="Sonu Nigam, Shreya Ghoshal",
            year="1983",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / (
                "Bhalo Lage Swapnoke - Hero (1983) - Sonu Nigam, Shreya Ghoshal.mp3"
            )
            track.write_bytes(b"media")

            report = enrich_folder_metadata(directory, workers=1)

            self.assertEqual(report.updated, (track,))
            replace_mock.assert_called_once_with(
                track,
                {},
                artwork_path="https://covers.example/hero.jpg",
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_album_art",
        return_value="https://covers.example/bhoot-bangla.jpg",
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.verify_metadata_evidence",
        return_value=MetadataVerificationDecision(
            "review",
            {},
            "",
            0.0,
            "Catalog compilation conflicts with protected album",
            (),
            ("catalog: album conflicts with the protected existing album",),
        ),
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={
            "title": "Tu Hi Disda",
            "album": "Love on Repeat",
            "artists": "Pritam, Arijit Singh, Nikhita Gandhi, Kumaar",
            "year": "2026",
            "album_art": "https://covers.example/love-on-repeat.jpg",
        },
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_album_folder_repairs_prior_compilation_tag_and_wrong_artwork(
        self,
        read_mock,
        _catalog_mock,
        _verify_mock,
        album_art_mock,
        replace_mock,
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Tu Hi Disda",
            album="Love on Repeat (2026)",
            artists="Arijit Singh, Nikhita Gandhi",
            year="2026",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            album_folder = Path(directory) / "Bhoot Bangla (2026)"
            album_folder.mkdir()
            track = album_folder / (
                "Tu Hi Disda - Love on Repeat (2026) - "
                "Arijit Singh, Nikhita Gandhi.mp3"
            )
            track.write_bytes(b"media")

            report = enrich_folder_metadata(
                directory, workers=1, agentic_model="configured-model"
            )

            repaired = album_folder / (
                "Tu Hi Disda - Bhoot Bangla (2026) - "
                "Arijit Singh, Nikhita Gandhi.mp3"
            )
            self.assertEqual(report.updated, (repaired,))
            album_art_mock.assert_called_once_with(
                "Bhoot Bangla", release_year="2026"
            )
            replace_mock.assert_called_once_with(
                track,
                {"album": "Bhoot Bangla (2026)"},
                artwork_path="https://covers.example/bhoot-bangla.jpg",
            )

    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.replace_media_metadata")
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_catalog_song_metadata",
        return_value={
            "title": "Kichu Hashi Kichu Asha (Original)",
            "album": "Bandhan (Original Motion Picture Soundtrack) [Original]",
            "artists": "Sonu Nigam",
            "year": "2004",
            "album_art": "https://covers.example/bandhan.jpg",
        },
    )
    @patch(
        "youtube_audio_video_downloader.services.album_metadata_enricher.find_wikipedia_song_metadata",
        return_value={
            "title": "Kichu Hashi Kichu Asha",
            "album": "Bandhan",
            "artists": "Sonu Nigam",
            "year": "2004",
        },
    )
    @patch("youtube_audio_video_downloader.services.album_metadata_enricher.read_media_metadata")
    def test_replaces_modern_lofi_metadata_with_verified_original(
        self, read_mock, _wiki_mock, _catalog_mock, replace_mock
    ) -> None:
        read_mock.return_value = EditableMediaMetadata(
            title="Kichhu Hashi Kichhu Asha - Lofi",
            album="Kichhu Hashi Kichhu Asha - Lofi",
            artists="Sonu Nigam",
            year="2025",
            artwork_present=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            track = Path(directory) / "old.mp3"
            track.write_bytes(b"media")

            report = enrich_folder_metadata(directory, workers=1)

            renamed = Path(directory) / (
                "Kichu Hashi Kichu Asha - Bandhan (2004) - Sonu Nigam.mp3"
            )
            self.assertEqual(report.updated, (renamed,))
            replace_mock.assert_called_once_with(
                track,
                {
                    "title": "Kichu Hashi Kichu Asha",
                    "album": "Bandhan (2004)",
                    "year": "2004",
                },
                artwork_path="https://covers.example/bandhan.jpg",
            )

    @patch("youtube_audio_video_downloader.gui.operations.enrich_folder_metadata")
    def test_gui_operation_reports_parallel_enrichment(self, enrich_mock) -> None:
        enrich_mock.return_value = MetadataEnrichmentReport(
            scanned=4,
            updated=(Path("one.mp3"), Path("two.mp3")),
            skipped=("complete.mp3",),
            failed=("failed.mp3",),
        )
        summary = execute_operation(
            "album_metadata_enricher",
            {"source_folder": "incoming", "workers": 6},
            CancellationToken(),
        )
        self.assertEqual(summary.operation, "album_metadata_enricher")
        self.assertEqual(summary.tagged, 2)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(enrich_mock.call_args.kwargs["workers"], 6)
        self.assertEqual(enrich_mock.call_args.kwargs["additional_folders"], ("",))

    @patch(
        "youtube_audio_video_downloader.gui.operations._reorder_album_from_wikipedia",
        return_value=2,
    )
    @patch(
        "youtube_audio_video_downloader.gui.operations.read_media_metadata",
        return_value=EditableMediaMetadata(album="Saawariya"),
    )
    @patch("youtube_audio_video_downloader.gui.operations.enrich_folder_metadata")
    def test_album_enricher_applies_wikipedia_order_to_every_completed_album_folder(
        self, enrich_mock, _read_mock, reorder_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "Saawariya (2007)"
            folder.mkdir()
            enrich_mock.return_value = MetadataEnrichmentReport(
                scanned=2,
                updated=(),
                skipped=(),
                failed=(),
                completed=(folder / "one.mp3", folder / "two.mp3"),
            )

            summary = execute_operation(
                "album_metadata_enricher",
                {
                    "source_folder": directory,
                    "wikipedia_track_order": True,
                    "retries": 4,
                },
                CancellationToken(),
            )

            self.assertEqual(summary.reordered, 2)
            reorder_mock.assert_called_once_with(folder, "Saawariya", retries=4)


if __name__ == "__main__":
    unittest.main()
