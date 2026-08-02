"""Jukebox-specific per-song metadata normalization."""

from __future__ import annotations

import unittest

from youtube_audio_video_downloader.services.jukebox_splitter import (
    YouTubeJukeboxSplitter,
)


class JukeboxMetadataTest(unittest.TestCase):
    def test_each_track_keeps_its_own_album_and_formatted_artists(self) -> None:
        errors: list[str] = []
        tracks = YouTubeJukeboxSplitter._parse_jukebox_track_specs(
            {
                "tracks": [
                    {
                        "First": {
                            "start": "00:00",
                            "end": "01:00",
                            "album": "First Album",
                            "artists": "Singer One & Singer Two",
                        }
                    },
                    {
                        "Second": {
                            "start": "01:00",
                            "end": "02:00",
                            "album": "Second Album",
                            "artists": "Singer Three and Singer Four",
                        }
                    },
                ]
            },
            "Compilation",
            errors,
        )

        self.assertEqual(errors, [])
        self.assertEqual(tracks[0].album, "First Album")
        self.assertEqual(tracks[0].artists, ["Singer One", "Singer Two"])
        self.assertEqual(tracks[1].album, "Second Album")
        self.assertEqual(tracks[1].artists, ["Singer Three", "Singer Four"])


if __name__ == "__main__":
    unittest.main()
