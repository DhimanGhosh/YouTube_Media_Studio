"""Tests for safe individual-track version matching."""

from __future__ import annotations

import unittest

from youtube_audio_video_downloader.services.individual_track_search import (
    _variant_compatible,
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


if __name__ == "__main__":
    unittest.main()
