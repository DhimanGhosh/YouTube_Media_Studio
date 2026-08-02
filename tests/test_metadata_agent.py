"""Tests for evidence-bounded agentic metadata decisions."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.services.ai_provider import AIResponse
from youtube_audio_video_downloader.services.metadata_agent import adjudicate_metadata


def response(data: dict[str, object]) -> AIResponse:
    return AIResponse(data, "Ollama", "qwen")


class MetadataAgentTest(unittest.TestCase):
    @patch("youtube_audio_video_downloader.services.metadata_agent.chat_json")
    def test_accepts_high_confidence_decision_using_supplied_evidence(self, chat_mock) -> None:
        chat_mock.return_value = response({
            "action": "apply", "title": "Song", "album": "Film",
            "artists": "Singer", "year": "2006", "language": "Bengali",
            "confidence": 0.96, "reason": "Sources agree",
            "sources": ["wikipedia", "catalog"],
        })
        result = adjudicate_metadata(
            {},
            {"title": "Song", "album": "Film", "artists": "Singer", "year": "2006", "language": "Bengali"},
            {"title": "Song", "album": "Film", "artists": "Singer", "year": "2006"},
            model="qwen",
        )
        self.assertEqual(result.action, "apply")
        self.assertEqual(result.metadata["album"], "Film")

    @patch("youtube_audio_video_downloader.services.metadata_agent.chat_json")
    def test_rejects_an_invented_album_even_at_high_confidence(self, chat_mock) -> None:
        chat_mock.return_value = response({
            "action": "apply", "title": "Song", "album": "Invented Film",
            "artists": "Singer", "year": "2006", "language": "",
            "confidence": 0.99, "reason": "Guess", "sources": ["wikipedia"],
        })
        result = adjudicate_metadata(
            {}, {"title": "Song", "album": "Real Film", "artists": "Singer", "year": "2006"},
            {}, model="qwen",
        )
        self.assertEqual(result.action, "review")
        self.assertIn("invented", result.reason.lower())

    @patch("youtube_audio_video_downloader.services.metadata_agent.chat_json")
    def test_discards_invented_optional_language_without_losing_identity(
        self, chat_mock
    ) -> None:
        chat_mock.return_value = response({
            "action": "apply", "title": "Song", "album": "Film",
            "artists": "Singer", "year": "2006", "language": "Hindi",
            "confidence": 0.96, "reason": "Wikipedia row matches",
            "sources": ["wikipedia"],
        })

        result = adjudicate_metadata(
            {},
            {"title": "Song", "album": "Film", "artists": "Singer", "year": "2006"},
            {},
            model="qwen",
        )

        self.assertEqual(result.action, "apply")
        self.assertEqual(result.metadata["language"], "")
        self.assertIn("ignored unsupported optional language", result.reason)

    @patch("youtube_audio_video_downloader.services.metadata_agent.chat_json")
    def test_album_conflict_requires_duration_confirmation(self, chat_mock) -> None:
        chat_mock.return_value = response({
            "action": "apply", "title": "Song", "album": "Catalog Film",
            "artists": "Singer", "year": "2017", "language": "",
            "confidence": 0.95, "reason": "Catalog selected", "sources": ["catalog"],
        })
        result = adjudicate_metadata(
            {}, {"title": "Song", "album": "Wiki Film", "artists": "Singer", "year": "2006"},
            {"title": "Song", "album": "Catalog Film", "artists": "Singer", "year": "2017"},
            model="qwen", catalog_duration_matches=False,
        )
        self.assertEqual(result.action, "review")
        self.assertLess(result.confidence, 0.85)

    @patch("youtube_audio_video_downloader.services.metadata_agent.chat_json")
    def test_accepts_serpapi_fields_without_allowing_invention(self, chat_mock) -> None:
        chat_mock.return_value = response({
            "action": "apply", "title": "Jonaki", "album": "Lorai",
            "artists": "Papon", "year": "2014", "language": "",
            "confidence": 0.94, "reason": "Exact Google evidence",
            "sources": ["serpapi"],
        })

        result = adjudicate_metadata(
            {}, {}, {},
            serpapi={
                "title": "Jonaki", "album": "Lorai",
                "artists": "Papon", "year": "2014",
            },
            model="qwen",
        )

        self.assertEqual(result.action, "apply")
        self.assertEqual(result.metadata["album"], "Lorai")
        self.assertEqual(result.sources, ("serpapi",))


if __name__ == "__main__":
    unittest.main()
