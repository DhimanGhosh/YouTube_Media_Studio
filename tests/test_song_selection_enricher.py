"""Tests for selected YouTube song metadata enrichment."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from youtube_audio_video_downloader.services.song_selection_enricher import (
    _find_external_metadata,
    enrich_selected_song,
)


class SongSelectionEnricherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.external_metadata = patch(
            "youtube_audio_video_downloader.services.song_selection_enricher._find_external_metadata",
            return_value={},
        ).start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def _downloader_with_info(info: dict) -> MagicMock:
        downloader = MagicMock()
        downloader.__enter__.return_value = downloader
        downloader.extract_info.return_value = info
        return downloader

    def test_title_cases_metadata_and_uses_youtube_upload_year(self) -> None:
        downloader = self._downloader_with_info(
            {
                "title": "ZINDAGI AA RAHA HOON MAIN",
                "track": "ZINDAGI AA RAHA HOON MAIN",
                "album": "ZINDAGI AA RAHA HOON MAIN",
                "artists": ["ATIF ASLAM", "AMAAL MALLIK", "MANOJ MUNTASHIR"],
                "uploader": "Atif Aslam",
                "upload_date": "20150616",
                "thumbnail": "https://youtube.example/thumb.jpg",
            }
        )
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher.find_song_art",
                return_value="https://covers.example/song.jpg",
            ) as art_finder,
        ):
            result = enrich_selected_song(
                "https://www.youtube.com/watch?v=example",
                title="zindagi aa raha hoon main atif",
                album="unknown",
                artists="atif aslam",
            )

        self.assertEqual(result["title"], "Zindagi Aa Raha Hoon Main")
        self.assertEqual(result["album"], "Zindagi Aa Raha Hoon Main")
        self.assertEqual(result["artists"], "Atif Aslam")
        self.assertEqual(result["release_year"], "2015")
        self.assertEqual(result["album_art"], "https://covers.example/song.jpg")
        art_finder.assert_called_once_with(
            "Zindagi Aa Raha Hoon Main", "Atif Aslam"
        )

    def test_uses_largest_youtube_thumbnail_when_art_search_fails(self) -> None:
        downloader = self._downloader_with_info(
            {
                "upload_date": "20210102",
                "thumbnails": [
                    {"url": "https://youtube.example/small.jpg", "width": 120, "height": 90},
                    {"url": "https://youtube.example/large.jpg", "width": 1280, "height": 720},
                ],
            }
        )
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher.find_song_art",
                side_effect=LookupError("not found"),
            ),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher._resolve_with_ollama",
                side_effect=ConnectionError("Ollama unavailable"),
            ),
        ):
            result = enrich_selected_song(
                "https://www.youtube.com/watch?v=example",
                title="example song",
                album="example album",
                artists="example artist",
                thumbnail="https://search.example/thumb.jpg",
            )

        self.assertEqual(result["album_art"], "https://youtube.example/large.jpg")

    def test_resolves_promotional_youtube_title_into_music_fields(self) -> None:
        downloader = self._downloader_with_info(
            {
                "title": (
                    "'Zindagi Aa Raha Hoon Main' Full AUDIO Song | "
                    "Atif Aslam, Tiger Shroff | T-Series"
                ),
                "channel": "T-Series",
                "upload_date": "20150513",
                "thumbnail": "https://youtube.example/thumb.jpg",
            }
        )
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher._resolve_with_ollama",
                return_value={
                    "title": "Zindagi Aa Raha Hoon Main",
                    "album": "Unknown",
                    "artists": "Atif Aslam, Tiger Shroff",
                },
            ),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher.find_song_art",
                return_value="https://covers.example/song.jpg",
            ),
        ):
            result = enrich_selected_song(
                "https://www.youtube.com/watch?v=example",
                title="zindagi aa raha hoon main",
                album="unknown",
                artists="atif aslam",
                request_text="zindagi aa raha hoon main atif",
            )

        self.assertEqual(result["title"], "Zindagi Aa Raha Hoon Main")
        self.assertEqual(result["album"], "Zindagi Aa Raha Hoon Main")
        self.assertEqual(result["artists"], "Atif Aslam")

    def test_promotional_title_has_a_clean_fallback_without_ollama(self) -> None:
        downloader = self._downloader_with_info(
            {
                "title": "'Example Song' Full Audio Song | Singer | Music Label",
                "channel": "Music Label",
                "upload_date": "20200101",
            }
        )
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher._resolve_with_ollama",
                side_effect=ConnectionError("Ollama unavailable"),
            ),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher.find_song_art",
                side_effect=LookupError("not found"),
            ),
        ):
            result = enrich_selected_song(
                "https://www.youtube.com/watch?v=example",
                title="example song singer",
                album="unknown",
                artists="singer",
            )

        self.assertEqual(result["title"], "Example Song")
        self.assertEqual(result["album"], "Example Song")
        self.assertEqual(result["artists"], "Singer")

    def test_exact_external_evidence_replaces_upload_year_and_supplies_art(self) -> None:
        downloader = self._downloader_with_info(
            {
                "title": "Bhalo Lage Swapnoke - Sonu Nigam Shreya Ghoshal",
                "track": "Bhalo Lage Swapnoke",
                "album": "Hero",
                "artists": ["Sonu Nigam", "Shreya Ghoshal"],
                "upload_date": "20210401",
            }
        )
        self.external_metadata.return_value = {
            "title": "Bhalo Lage Swapnoke",
            "album": "Hero",
            "year": "2006",
            "album_art": "https://covers.example/hero.jpg",
        }
        with (
            patch("yt_dlp.YoutubeDL", return_value=downloader),
            patch(
                "youtube_audio_video_downloader.services.song_selection_enricher.find_song_art"
            ) as art_finder,
        ):
            result = enrich_selected_song(
                "https://www.youtube.com/watch?v=example",
                title="Bhalo Lage Swapnoke",
                album="Hero",
                artists="Sonu Nigam, Shreya Ghoshal",
            )

        self.assertEqual(result["release_year"], "2006")
        self.assertEqual(result["album_art"], "https://covers.example/hero.jpg")
        art_finder.assert_not_called()

    def test_agent_adjudicates_even_when_external_sources_agree(self) -> None:
        """Agreement is evidence for the agent to validate, not a reason to bypass it."""

        wikipedia = {
            "title": "Bhalo Lage Swapnoke",
            "album": "Hero",
            "artists": "Sonu Nigam, Shreya Ghoshal",
            "year": "2006",
            "language": "Bengali",
        }
        catalog = {
            **wikipedia,
            "album_art": "https://covers.example/hero.jpg",
        }
        decision = MagicMock(
            action="apply",
            metadata=wikipedia,
            album_art="https://covers.example/hero.jpg",
        )
        with patch(
            "youtube_audio_video_downloader.services.song_selection_enricher."
            "find_wikipedia_song_metadata",
            return_value=wikipedia,
        ), patch(
            "youtube_audio_video_downloader.services.song_selection_enricher."
            "find_catalog_song_metadata",
            return_value=catalog,
        ), patch(
            "youtube_audio_video_downloader.services.song_selection_enricher."
            "verify_metadata_evidence",
            return_value=decision,
        ) as verifier_mock:
            result = _find_external_metadata(
                raw_context="Bhalo Lage Swapnoke Sonu Nigam Shreya Ghoshal",
                title="Bhalo Lage Swapnoke",
                artists="Sonu Nigam, Shreya Ghoshal",
                model="metadata-agent:test",
            )

        verifier_mock.assert_called_once_with(
            {
                "title": "Bhalo Lage Swapnoke",
                "album": "",
                "artists": "Sonu Nigam, Shreya Ghoshal",
            },
            wikipedia,
            catalog,
            model="metadata-agent:test",
        )
        self.assertEqual(result["album"], "Hero")
        self.assertEqual(result["year"], "2006")
        self.assertEqual(result["album_art"], "https://covers.example/hero.jpg")


if __name__ == "__main__":
    unittest.main()
