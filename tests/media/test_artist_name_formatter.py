"""Shared artist-name normalization behavior."""

from __future__ import annotations

import unittest

from youtube_audio_video_downloader.utils.artist_name_formatter import (
    format_artist_names,
)


class ArtistNameFormatterTest(unittest.TestCase):
    def test_normalizes_ampersand_and_case_insensitive_and(self) -> None:
        self.assertEqual(
            format_artist_names(
                "Javed Ali&Sonu Nigam AND Shreya Ghoshal and Alka Yagnik"
            ),
            "Javed Ali, Sonu Nigam, Shreya Ghoshal, Alka Yagnik",
        )

    def test_removes_dots_from_initials_and_expands_known_short_names(self) -> None:
        self.assertEqual(
            format_artist_names(
                "K.K., K.K, KK, A. R. Rahman, A R Rahman, Arijit, Abhijeet"
            ),
            "KK, AR Rahman, Arijit Singh, Abhijeet Bhattacharya",
        )

    def test_preserves_periods_in_a_stage_name_that_is_not_initials(self) -> None:
        self.assertEqual(format_artist_names("will.i.am"), "will.i.am")

    def test_expands_known_duo_only_when_the_complete_credit_matches(self) -> None:
        self.assertEqual(
            format_artist_names("Vishal & Shekhar"),
            "Vishal Dadlani, Shekhar Ravjiani",
        )
        self.assertEqual(format_artist_names("Vishal"), "Vishal")
        self.assertEqual(format_artist_names("Shekhar"), "Shekhar")


if __name__ == "__main__":
    unittest.main()
