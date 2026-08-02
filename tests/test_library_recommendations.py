"""Grounding and request-boundary tests for local-library AI suggestions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from youtube_audio_video_downloader.services.ai_provider import AIResponse
from youtube_audio_video_downloader.services.library_recommendations import (
    MAX_LIBRARY_CANDIDATES,
    recommend_library_tracks,
)
from youtube_audio_video_downloader.services.media_library import LibraryItem


def track(path: str, title: str = "Song", artists: str = "Artist") -> LibraryItem:
    return LibraryItem(path, title, "Album", artists, 2020, 1000, "audio", 1)


def response(rows: list[dict[str, object]]) -> AIResponse:
    return AIResponse({"recommendations": rows}, "Ollama", "local-model")


class LibraryRecommendationsTest(unittest.TestCase):
    def test_only_indexed_ids_are_returned_and_local_availability_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            local_path = Path(folder) / "local.mp3"
            local_path.write_bytes(b"audio")
            items = [
                track(str(local_path), "Local"),
                track(str(Path(folder) / "gone.mp3"), "Gone"),
            ]
            answer = response([
                {"id": 0, "reason": "Artist matches"},
                {"id": 999, "reason": "Invented"},
                {"id": 1, "reason": "Same album"},
                {"id": 0, "reason": "Duplicate"},
            ])
            with patch(
                "youtube_audio_video_downloader.services.library_recommendations.chat_json",
                return_value=answer,
            ):
                result = recommend_library_tracks("artist songs", items, model="local-model")
            self.assertEqual([value.item.title for value in result], ["Gone", "Local"])
            self.assertEqual([value.exists_locally for value in result], [False, True])

    def test_request_is_bounded_and_contains_metadata_but_not_local_paths(self) -> None:
        items = [track(f"C:/secret/{index}.mp3", f"Song {index}") for index in range(800)]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations.chat_json",
            return_value=response([]),
        ) as chat_mock:
            recommend_library_tracks("calm mood", items, model="global-model")

        messages = chat_mock.call_args.args[0]
        supplied = json.loads(messages[1]["content"])["catalog"]
        self.assertEqual(chat_mock.call_args.kwargs["model"], "global-model")
        self.assertEqual(len(supplied), MAX_LIBRARY_CANDIDATES)
        self.assertNotIn("path", supplied[0])
        self.assertEqual(
            set(supplied[0]), {"id", "title", "artists", "album", "year", "type"}
        )

    def test_missing_prompt_or_global_model_is_rejected_before_network(self) -> None:
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations.chat_json"
        ) as chat_mock:
            with self.assertRaisesRegex(ValueError, "Describe"):
                recommend_library_tracks("", [track("x.mp3")], model="model")
            with self.assertRaisesRegex(ValueError, "Global Settings"):
                recommend_library_tracks("mood", [track("x.mp3")], model="")
        chat_mock.assert_not_called()

    def test_explicit_artist_request_excludes_other_artists_before_ai(self) -> None:
        items = [
            track("atif.mp3", "Atif Song", "Atif Aslam"),
            track("kk.mp3", "KK Song", "KK"),
        ]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations.chat_json",
            return_value=response([{"id": 0, "reason": "invented KK song"}]),
        ) as chat_mock:
            result = recommend_library_tracks("atif songs", items, model="model")

        catalog = json.loads(chat_mock.call_args.args[0][1]["content"])["catalog"]
        self.assertEqual([row["artists"] for row in catalog], ["Atif Aslam"])
        self.assertEqual([value.item.artists for value in result], ["Atif Aslam"])
        self.assertEqual(result[0].reason, "Artist matches Atif Aslam")
        self.assertNotIn("invented", result[0].reason)

    def test_requested_artist_with_no_matching_local_track_skips_ai(self) -> None:
        items = [track("atif.mp3", "Atif Song", "Atif Aslam")]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations.chat_json"
        ) as chat_mock:
            result = recommend_library_tracks("KK songs", items, model="model")
        self.assertEqual(result, [])
        chat_mock.assert_not_called()

    def test_ai_outage_uses_deterministic_metadata_ranking(self) -> None:
        items = [track("one.mp3", "Calm One"), track("two.mp3", "Other")]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations.chat_json",
            side_effect=RuntimeError("offline"),
        ):
            result = recommend_library_tracks("calm mood", items, model="model", limit=1)
        self.assertEqual([item.item.title for item in result], ["Calm One"])


if __name__ == "__main__":
    unittest.main()
