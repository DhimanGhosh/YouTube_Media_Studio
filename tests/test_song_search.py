"""Offline tests for natural-language song request understanding."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.services.downloads.song_search import (
    _intent_from_payload,
    _local_intent,
    routed_result_title,
    understand_song_request,
)
from youtube_audio_video_downloader.services.ai.ai_provider import AIResponse


class SongSearchTest(unittest.TestCase):
    def test_jukebox_route_preserves_selected_youtube_title(self) -> None:
        selected = "Gangster Movie Non-Stop Songs | Romantic Collection"
        self.assertEqual(
            routed_result_title(selected, "Hindi Full Album", "jukebox"),
            selected,
        )

    @patch("youtube_audio_video_downloader.services.downloads.song_search.chat_json")
    def test_ai_disabled_never_calls_a_model(self, chat_mock) -> None:
        intent = understand_song_request("Hawayein by Arijit Singh", use_ai=False)

        chat_mock.assert_not_called()
        self.assertEqual(intent.engine, "internet + local rules")
        self.assertIn("AI disabled", intent.explanation)

    def test_local_parser_extracts_song_artist_movie_and_year(self) -> None:
        intent = _local_intent(
            "Find Tumko Dekha Toh by Kumar Sanu from the movie "
            "Hamara Dil Aapke Paas Hai 2000"
        )
        self.assertEqual(intent.title, "Tumko Dekha Toh")
        self.assertEqual(intent.artists, "Kumar Sanu")
        self.assertEqual(intent.movie, "Hamara Dil Aapke Paas Hai")
        self.assertEqual(intent.release_year, "2000")
        self.assertEqual(intent.workflow, "audio")
        self.assertIn("official audio", intent.search_query)

    @patch("youtube_audio_video_downloader.services.downloads.song_search.chat_json")
    def test_ai_search_intent_is_independently_reviewed(self, chat_mock) -> None:
        extracted = {
            "title": "Gangster Hindi Full Album",
            "artists": "",
            "album": "",
            "movie": "",
            "release_year": "",
            "workflow": "jukebox",
            "search_query": "gangster hindi full album audio jukebox",
            "explanation": "Compilation request.",
        }
        reviewed = {
            **extracted,
            "title": "Gangster",
            "album": "Gangster",
            "explanation": "Independent review separated the album identity.",
        }
        chat_mock.side_effect = [
            AIResponse(extracted, "NVIDIA NIM", "agent:test"),
            AIResponse(reviewed, "NVIDIA NIM", "agent:test"),
        ]

        intent = understand_song_request(
            "gangster hindi full album audio jukebox",
            model="agent:test",
        )

        self.assertEqual(chat_mock.call_count, 2)
        self.assertEqual(intent.title, "Gangster")
        self.assertEqual(intent.album, "Gangster")
        self.assertIn("independent reviewer", intent.engine)

    def test_local_parser_routes_full_album_to_album_splitter(self) -> None:
        intent = _local_intent("Download the full album Raabta 2017")
        self.assertEqual(intent.workflow, "album")
        self.assertIn("full album audio jukebox", intent.search_query)

    def test_structured_llm_payload_is_normalized(self) -> None:
        intent = _intent_from_payload(
            {
                "title": "Hawayein",
                "artists": "Arijit Singh",
                "album": "Jab Harry Met Sejal",
                "movie": "",
                "release_year": "2017",
                "workflow": "audio",
                "search_query": "Hawayein Arijit Singh official audio",
                "explanation": "Single-song audio request.",
            },
            engine="Ollama · test-model",
        )
        self.assertEqual(intent.album, "Jab Harry Met Sejal")
        self.assertEqual(intent.engine, "Ollama · test-model")

    def test_model_artist_text_is_removed_from_the_end_of_the_title(self) -> None:
        intent = _intent_from_payload(
            {
                "title": "zindagi aa raha hoon main atif",
                "artists": "atif aslam",
                "album": "",
                "movie": "",
                "release_year": "",
                "workflow": "audio",
                "search_query": "zindagi aa raha hoon main atif aslam official audio",
                "explanation": "Song request.",
            },
            engine="test-model",
        )

        self.assertEqual(intent.title, "zindagi aa raha hoon main")
        self.assertEqual(intent.artists, "atif aslam")

    def test_model_cannot_route_movie_context_to_video_without_video_request(self) -> None:
        intent = _intent_from_payload(
            {
                "title": "Hawayein",
                "artists": "Arijit Singh",
                "album": "Jab Harry Met Sejal (2017)",
                "movie": "Jab Harry Met Sejal (2017)",
                "release_year": "",
                "workflow": "video",
                "search_query": "Hawayein Arijit Singh music video",
                "explanation": "Video request.",
            },
            engine="Ollama · test-model",
            request_text=(
                "Find Hawayein by Arijit Singh from the movie Jab Harry Met Sejal 2017"
            ),
        )
        self.assertEqual(intent.workflow, "audio")
        self.assertEqual(intent.album, "")
        self.assertEqual(intent.movie, "Jab Harry Met Sejal")
        self.assertEqual(intent.release_year, "2017")
        self.assertIn("official audio", intent.search_query)


if __name__ == "__main__":
    unittest.main()
