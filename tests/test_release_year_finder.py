"""Tests for Wikipedia album release-year parsing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.services.release_year_finder import (
    extract_release_year,
    find_album_release_year,
)


class ReleaseYearFinderTest(unittest.TestCase):
    def test_extracts_year_from_released_infobox_field(self) -> None:
        text = "{{Infobox album\n| recorded = 2016\n| released = {{Start date|2017|6|3}}\n}}"
        self.assertEqual(extract_release_year(text), "2017")

    def test_does_not_use_unrelated_year(self) -> None:
        self.assertEqual(extract_release_year("Released in prose in 2017"), "")

    @patch("youtube_audio_video_downloader.services.release_year_finder._wikipedia_api")
    def test_retries_with_exact_film_query_when_soundtrack_search_is_noisy(
        self, api_mock
    ) -> None:
        api_mock.side_effect = [
            {"query": {"search": [{"title": "Rajesh Khanna"}]}},
            {"query": {"search": [{"title": "Aa Ab Laut Chalen"}]}},
            {
                "query": {
                    "pages": [{
                        "revisions": [{
                            "slots": {"main": {"content": "| release_date = 22 January 1999"}}
                        }]
                    }]
                }
            },
        ]

        result = find_album_release_year("Aa Ab Laut Chalen")

        self.assertEqual(result["year"], "1999")

    @patch("youtube_audio_video_downloader.services.release_year_finder._wikipedia_api")
    def test_find_again_skips_current_year(self, api_mock) -> None:
        api_mock.side_effect = [
            {
                "query": {
                    "search": [
                        {"title": "Example (1999 film)"},
                        {"title": "Example (2001 film)"},
                    ]
                }
            },
            {
                "query": {
                    "pages": [{
                        "revisions": [{
                            "slots": {"main": {"content": "| released = 1999"}}
                        }]
                    }]
                }
            },
            {
                "query": {
                    "pages": [{
                        "revisions": [{
                            "slots": {"main": {"content": "| released = 2001"}}
                        }]
                    }]
                }
            },
        ]

        result = find_album_release_year("Example", exclude_year="1999")

        self.assertEqual(result["year"], "2001")


if __name__ == "__main__":
    unittest.main()
