"""Tests for Google Images album-art result parsing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.services.album_art_finder import (
    extract_square_image_urls,
    find_album_art,
    find_catalog_song_metadata,
    _find_catalog_song_art,
    _find_exact_youtube_thumbnail,
)


class AlbumArtFinderTest(unittest.TestCase):

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_art_fallback_requires_exact_title_and_artist(self, ydl_class) -> None:
        ydl_class.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [
                {"id": "unrelated01", "title": "Different Song", "channel": "Artist"},
                {
                    "id": "8qyR4_xeQCw",
                    "title": "Chine Phelechhi Rastaghat | Arijit Singh | Anweshaa",
                    "channel": "Asha Audio",
                    "channel_is_verified": True,
                },
            ]
        }
        self.assertEqual(
            _find_exact_youtube_thumbnail(
                "Chine Phelechhi Rastaghat", "Arijit Singh, Anweshaa"
            ),
            "https://i.ytimg.com/vi/8qyR4_xeQCw/hqdefault.jpg",
        )

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_art_requires_every_multi_artist_hint(self, ydl_class) -> None:
        ydl_class.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [
                {
                    "id": "wrongmatch1",
                    "title": "Oh My Love | Sonu Nigam | Shreya Ghoshal",
                    "channel": "T-Series",
                    "channel_is_verified": True,
                },
                {
                    "id": "rightmatch1",
                    "title": "Oh My Love | Kunal Ganjawala | Shreya Ghoshal",
                    "channel": "SVF Music",
                },
            ]
        }

        self.assertEqual(
            _find_exact_youtube_thumbnail(
                "Oh My Love", "Kunal Ganjawala, Shreya Ghoshal"
            ),
            "https://i.ytimg.com/vi/rightmatch1/hqdefault.jpg",
        )

    def test_returns_square_original_images_in_result_order(self) -> None:
        page = r'''
            ["https://example.com/wide.jpg",1200,630]
            ["https://example.com/first-cover.jpg",500,500]
            ["https://example.com/second-cover.png",1000,1000]
        '''
        self.assertEqual(
            extract_square_image_urls(page),
            [
                "https://example.com/first-cover.jpg",
                "https://example.com/second-cover.png",
            ],
        )

    def test_ignores_google_thumbnails_and_duplicates(self) -> None:
        page = r'''
            ["https://encrypted-tbn0.gstatic.com/images?q=x",225,225]
            ["https://example.com/cover.jpg",500,500]
            ["https://example.com/cover.jpg",500,500]
        '''
        self.assertEqual(extract_square_image_urls(page), ["https://example.com/cover.jpg"])

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    def test_catalog_lookup_returns_structured_exact_song_metadata(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return (
                    b'{"results":[{"trackName":"Chine Phelechhi Rastaghat",'
                    b'"artistName":"Arijit Singh & Anweshaa Dutta Gupta",'
                    b'"collectionName":"Aschhe Bachhor Abar Hobe",'
                    b'"releaseDate":"2015-05-01T00:00:00Z",'
                    b'"primaryGenreName":"Bengali",'
                    b'"trackTimeMillis":245000,'
                    b'"artworkUrl100":"https://example.com/100x100bb.jpg"}]}'
                )

        urlopen_mock.return_value = Response()
        result = find_catalog_song_metadata(
            "Chine Phelechhi Rastaghat", "Arijit Singh, Anweshaa"
        )
        self.assertEqual(result["album"], "Aschhe Bachhor Abar Hobe")
        self.assertEqual(result["year"], "2015")
        self.assertIn("1200x1200bb", result["album_art"])
        self.assertEqual(result["duration_seconds"], "245.0")
        self.assertEqual(result["language"], "Bengali")

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    def test_catalog_lookup_uses_parent_collection_year_and_artwork(self, urlopen_mock) -> None:
        class Response:
            def __init__(self, payload: bytes):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return self.payload

        urlopen_mock.side_effect = [
            Response(
                b'{"results":[{"trackName":"Mon Janala Khule Dena",'
                b'"artistName":"Kishore Kumar","collectionName":"Aamar Pujar Phool",'
                b'"collectionId":1532588847,"releaseDate":"1981-01-01T00:00:00Z",'
                b'"artworkUrl100":"https://wrong/100x100bb.jpg"}]}'
            ),
            Response(
                b'{"results":[{"wrapperType":"collection",'
                b'"collectionName":"Aamar Pujar Phool","releaseDate":"1982-01-01T00:00:00Z",'
                b'"artworkUrl100":"https://correct/100x100bb.jpg"}]}'
            ),
        ]

        result = find_catalog_song_metadata("Mon Janala Khule Dena", "Kishore Kumar")

        self.assertEqual(result["year"], "1982")
        self.assertEqual(result["album"], "Aamar Pujar Phool")
        self.assertEqual(result["album_art"], "https://correct/1200x1200bb.jpg")

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    def test_catalog_metadata_requires_every_multi_artist_hint(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return (
                    b'{"results":['
                    b'{"trackName":"Oh My Love","artistName":"Sonu Nigam & Shreya Ghoshal",'
                    b'"collectionName":"Raaz 3","releaseDate":"2012-01-01"},'
                    b'{"trackName":"Oh My Love","artistName":"Kunal Ganjawala & Shreya Ghoshal",'
                    b'"collectionName":"Amanush","releaseDate":"2010-01-01"}]}'
                )

        urlopen_mock.return_value = Response()

        result = find_catalog_song_metadata(
            "Oh My Love", "Kunal Ganjawala, Shreya Ghoshal"
        )

        self.assertEqual(result["album"], "Amanush")
        self.assertEqual(result["year"], "2010")

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    def test_catalog_song_art_requires_every_multi_artist_hint(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return (
                    b'{"results":['
                    b'{"trackName":"Oh My Love","artistName":"Sonu Nigam & Shreya Ghoshal",'
                    b'"artworkUrl100":"https://wrong/100x100bb.jpg"},'
                    b'{"trackName":"Oh My Love","artistName":"Kunal Ganjawala & Shreya Ghoshal",'
                    b'"artworkUrl100":"https://right/100x100bb.jpg"}]}'
                )

        urlopen_mock.return_value = Response()

        self.assertEqual(
            _find_catalog_song_art(
                "Oh My Love", "Kunal Ganjawala, Shreya Ghoshal", 12.0
            ),
            "https://right/1200x1200bb.jpg",
        )

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    def test_find_album_art_skips_current_catalog_cover(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return (
                    b'{"results":['
                    b'{"collectionName":"Example","artworkUrl100":'
                    b'"https://first/100x100bb.jpg"},'
                    b'{"collectionName":"Example (Original Soundtrack)",'
                    b'"artworkUrl100":"https://second/100x100bb.jpg"}]}'
                )

        urlopen_mock.return_value = Response()

        self.assertEqual(
            find_album_art(
                "Example",
                exclude_url="https://first/1200x1200bb.jpg",
            ),
            "https://second/1200x1200bb.jpg",
        )

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    @patch(
        "youtube_audio_video_downloader.services.serpapi_metadata."
        "find_serpapi_album_art",
        return_value="https://example.test/serpapi-cover.jpg",
    )
    @patch(
        "youtube_audio_video_downloader.services.album_art_finder."
        "_find_catalog_album_art",
        side_effect=OSError("catalog unavailable"),
    )
    def test_find_album_art_uses_serpapi_before_unauthenticated_google(
        self, _catalog_mock, serpapi_mock, google_open_mock
    ) -> None:
        result = find_album_art("Lorai", release_year="2014")

        self.assertEqual(result, "https://example.test/serpapi-cover.jpg")
        serpapi_mock.assert_called_once_with(
            "Lorai", "2014", 12.0, exclude_url=""
        )
        google_open_mock.assert_not_called()

    @patch("youtube_audio_video_downloader.services.album_art_finder.urlopen")
    def test_catalog_metadata_can_skip_current_album(self, urlopen_mock) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self) -> bytes:
                return (
                    b'{"results":['
                    b'{"trackName":"Example Song","artistName":"Singer",'
                    b'"collectionName":"First Album"},'
                    b'{"trackName":"Example Song","artistName":"Singer",'
                    b'"collectionName":"Second Album"}]}'
                )

        urlopen_mock.return_value = Response()

        result = find_catalog_song_metadata(
            "Example Song",
            "Singer",
            exclude_album="First Album",
        )

        self.assertEqual(result["album"], "Second Album")


if __name__ == "__main__":
    unittest.main()
