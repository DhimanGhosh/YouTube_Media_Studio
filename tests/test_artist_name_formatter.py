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

    def test_preserves_comma_separated_names_and_dotted_initials(self) -> None:
        self.assertEqual(
            format_artist_names("K.K., A. R. Rahman"),
            "K.K., A. R. Rahman",
        )


if __name__ == "__main__":
    unittest.main()
