"""Grounding and workflow tests for agentic local-library suggestions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from youtube_audio_video_downloader.services.library_recommendations import (
    MAX_SEMANTIC_CANDIDATES,
    recommend_library_tracks,
)
from youtube_audio_video_downloader.services.media_library import LibraryItem


def track(
    path: str,
    title: str = "Song",
    artists: str = "Artist",
    *,
    album: str = "Album",
    year: int = 2020,
) -> LibraryItem:
    return LibraryItem(path, title, album, artists, year, 1000, "audio", 1)


def response(data: dict[str, object]) -> SimpleNamespace:
    def convert(value: object) -> object:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: convert(item) for key, item in value.items()})
        if isinstance(value, list):
            return [convert(item) for item in value]
        return value

    return convert(data)  # type: ignore[return-value]


def plan(
    *,
    artists: list[str] | None = None,
    languages: list[str] | None = None,
    semantic_filters: list[str] | None = None,
    time_preference: str = "any",
    use_web_evidence: bool = False,
) -> SimpleNamespace:
    return response(
        {
            "artists": artists or [],
            "languages": languages or [],
            "genres": [],
            "moods": [],
            "activities": [],
            "energy_or_tempo": [],
            "other_constraints": semantic_filters or [],
            "time_preference": time_preference,
            "use_web_evidence": use_web_evidence,
        }
    )


class LibraryRecommendationsTest(unittest.TestCase):
    def test_only_indexed_ids_are_returned_and_local_availability_is_checked(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            local_path = Path(folder) / "local.mp3"
            local_path.write_bytes(b"audio")
            items = [
                track(str(local_path), "Local"),
                track(str(Path(folder) / "gone.mp3"), "Gone"),
            ]
            with patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(),
                    response(
                        {
                            "matches": [
                                {"id": 0, "matches": True, "confidence": 0.9,
                                 "matched_filters": []},
                                {"id": 1, "matches": True, "confidence": 0.9,
                                 "matched_filters": []},
                            ]
                        }
                    ),
                    response({"ids": [1, 999, 0, 1]}),
                ],
            ):
                result = recommend_library_tracks("artist songs", items, model="local-model")
            self.assertEqual([value.item.title for value in result], ["Local", "Gone"])
            self.assertEqual([value.exists_locally for value in result], [True, False])

    def test_request_is_bounded_and_never_contains_local_paths(self) -> None:
        items = [track(f"C:/secret/{index}.mp3", f"Song {index}") for index in range(800)]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations."
            "run_structured_agent",
            side_effect=[
                plan(),
                response(
                    {
                        "matches": [
                            {"id": index, "matches": True, "confidence": 0.9,
                             "matched_filters": []}
                            for index in range(MAX_SEMANTIC_CANDIDATES)
                        ]
                    }
                ),
                response({"ids": []}),
            ],
        ) as chat_mock:
            recommend_library_tracks("calm mood", items, model="global-model")

        supplied = chat_mock.call_args_list[1].kwargs["input_data"]["catalog"]
        self.assertEqual(
            chat_mock.call_args_list[1].kwargs["requested_model"], "global-model"
        )
        self.assertEqual(len(supplied), MAX_SEMANTIC_CANDIDATES)
        self.assertNotIn("path", supplied[0])
        self.assertEqual(
            set(supplied[0]), {"id", "title", "artists", "album", "year", "type"},
        )
        curator_catalog = chat_mock.call_args_list[2].kwargs["input_data"]["catalog"]
        self.assertEqual(len(curator_catalog), MAX_SEMANTIC_CANDIDATES)

    def test_missing_prompt_or_global_model_is_rejected_before_network(self) -> None:
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations."
            "run_structured_agent"
        ) as chat_mock:
            with self.assertRaisesRegex(ValueError, "Describe"):
                recommend_library_tracks("", [track("x.mp3")], model="model")
            with self.assertRaisesRegex(ValueError, "Global Settings"):
                recommend_library_tracks("mood", [track("x.mp3")], model="")
        chat_mock.assert_not_called()

    def test_complex_request_runs_planner_verifier_and_curator(self) -> None:
        items = [
            track("ballad.mp3", "Quiet Love", "Arijit Singh", year=2025),
            track("dance.mp3", "Dance Hit", "Arijit Singh", year=2024),
            track("bengali.mp3", "Bangla Beat", "Arijit Singh", year=2023),
            track("other.mp3", "Club Hit", "Other Singer", year=2025),
        ]
        semantic = response(
            {
                "matches": [
                    {"id": 0, "matches": False, "confidence": 0.9, "matched_filters": ["Hindi"]},
                    {
                        "id": 1,
                        "matches": True,
                        "confidence": 0.94,
                        "matched_filters": ["Hindi", "dance", "high upbeat"],
                    },
                ]
            }
        )
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(
                        artists=["Arijit Singh"],
                        languages=["Hindi"],
                        semantic_filters=["dance", "high upbeat"],
                        time_preference="latest",
                        use_web_evidence=True,
                    ),
                    semantic,
                    response({"ids": [0]}),
                ],
            ) as chat_mock,
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={
                    1: {
                        "language": "Hindi",
                        "genre": "Bollywood dance",
                        "web_search_excerpts": "A high-energy upbeat dance track.",
                    }
                },
            ) as evidence_mock,
        ):
            result = recommend_library_tracks(
                "latest arijit singh hindi dance songs", items, model="model"
            )

        self.assertEqual([value.item.title for value in result], ["Dance Hit"])
        self.assertIn("Hindi", result[0].reason)
        self.assertIn("latest release (2024)", result[0].reason)
        self.assertEqual(chat_mock.call_count, 3)
        evidence_mock.assert_called_once()
        verifier_catalog = chat_mock.call_args_list[1].kwargs["input_data"]["catalog"]
        self.assertEqual({row["artists"] for row in verifier_catalog}, {"Arijit Singh"})
        self.assertNotIn("other.mp3", json.dumps(verifier_catalog))

    def test_old_language_request_uses_relative_library_years(self) -> None:
        items = [
            track("old-bengali.mp3", "Purono Gaan", "Singer A", year=1970),
            track("old-hindi.mp3", "Purana Gana", "Singer B", year=1980),
            track("new-bengali.mp3", "Notun Gaan", "Singer C", year=2024),
        ]
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(languages=["Bengali"], time_preference="older"),
                    response(
                        {
                            "matches": [
                                {
                                    "id": 0,
                                    "matches": True,
                                    "confidence": 0.9,
                                    "matched_filters": ["Bengali"],
                                },
                                {
                                    "id": 1,
                                    "matches": False,
                                    "confidence": 0.9,
                                    "matched_filters": [],
                                },
                            ]
                        }
                    ),
                    response({"ids": [0]}),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={0: {"language": "Bengali"}},
            ),
        ):
            result = recommend_library_tracks("old bengali songs", items, model="model")
        self.assertEqual([value.item.title for value in result], ["Purono Gaan"])
        self.assertIn("older release (1970)", result[0].reason)

    def test_language_claim_without_matching_evidence_is_rejected(self) -> None:
        items = [
            track("hindi.mp3", "Aur Mohabbat Kitni Karoon", "Singer A"),
            track("bengali.mp3", "Bengali Song", "Singer B"),
        ]
        semantic = response(
            {
                "matches": [
                    {
                        "id": index,
                        "matches": True,
                        "confidence": 0.95,
                        "matched_filters": ["Bengali", "slow"],
                    }
                    for index in range(2)
                ]
            }
        )
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(languages=["Bengali"], semantic_filters=["slow"]),
                    semantic,
                    response({"ids": [0]}),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                side_effect=lambda candidates, _filters: {
                    index: {
                        "language": (
                            "Hindi"
                            if item.title == "Aur Mohabbat Kitni Karoon"
                            else "Bengali"
                        ),
                        "web_search_excerpts": (
                            "A slow Bengali ballad."
                            if item.title == "Bengali Song"
                            else "A Hindi romantic song."
                        ),
                    }
                    for index, item in enumerate(candidates)
                },
            ),
        ):
            result = recommend_library_tracks("slow bengali songs", items, model="model")

        self.assertEqual([value.item.title for value in result], ["Bengali Song"])

    def test_slow_claim_is_rejected_when_evidence_says_upbeat(self) -> None:
        items = [
            track("slow.mp3", "Verified Slow Song", "Singer A"),
            track("upbeat.mp3", "Kichu Halka", "Singer B"),
        ]
        semantic = response(
            {
                "matches": [
                    {
                        "id": index,
                        "matches": True,
                        "confidence": 0.95,
                        "matched_filters": ["Bengali", "slow", "sleeping", "ballad"],
                    }
                    for index in range(2)
                ]
            }
        )
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(languages=["Bengali"], semantic_filters=["slow"]),
                    semantic,
                    response({"ids": [0]}),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                side_effect=lambda candidates, _filters: {
                    index: {
                        "language": "Bengali",
                        "web_search_excerpts": (
                            "A slow Bengali song with low tempo."
                            if item.title == "Verified Slow Song"
                            else "An upbeat high-energy Bengali travel song."
                        ),
                    }
                    for index, item in enumerate(candidates)
                },
            ),
        ):
            result = recommend_library_tracks("slow bengali songs", items, model="model")

        self.assertEqual([value.item.title for value in result], ["Verified Slow Song"])
        self.assertNotIn("sleeping", result[0].reason.casefold())
        self.assertNotIn("ballad", result[0].reason.casefold())

    def test_mix_continuation_preserves_language_but_relaxes_mood(self) -> None:
        items = [track("bengali.mp3", "Another Bengali Song", "Singer B")]
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(languages=["Bengali"], semantic_filters=["slow"]),
                    response(
                        {
                            "matches": [
                                {
                                    "id": 0,
                                    "matches": True,
                                    "confidence": 0.95,
                                    "matched_filters": ["Bengali"],
                                }
                            ]
                        }
                    ),
                    response({"ids": [0]}),
                ],
            ) as agent_mock,
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={0: {"language": "Bengali"}},
            ),
        ):
            result = recommend_library_tracks(
                "slow bengali songs",
                items,
                model="model",
                language_continuation=True,
            )

        verifier_call = agent_mock.call_args_list[1]
        self.assertEqual(verifier_call.kwargs["input_data"]["filters"], ("Bengali",))
        self.assertEqual(
            verifier_call.kwargs["input_data"]["request"],
            "Songs in requested language(s): Bengali",
        )
        self.assertEqual([value.item.title for value in result], ["Another Bengali Song"])

    def test_mix_continuation_without_a_planned_language_stops_after_planning(self) -> None:
        items = [track("song.mp3", "A Song", "Singer")]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations."
            "run_structured_agent",
            return_value=plan(semantic_filters=["slow"]),
        ) as agent_mock:
            result = recommend_library_tracks(
                "slow songs",
                items,
                model="model",
                language_continuation=True,
            )

        self.assertEqual(result, [])
        agent_mock.assert_called_once()

    def test_requested_artist_with_no_matching_local_track_stops_after_planning(self) -> None:
        items = [track("atif.mp3", "Atif Song", "Atif Aslam")]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations."
            "run_structured_agent",
            return_value=plan(artists=["KK"]),
        ) as chat_mock:
            result = recommend_library_tracks("KK songs", items, model="model")
        self.assertEqual(result, [])
        chat_mock.assert_called_once()

    def test_ai_outage_uses_deterministic_local_ranking(self) -> None:
        items = [track("one.mp3", "Calm One"), track("two.mp3", "Other")]
        with patch(
            "youtube_audio_video_downloader.services.library_recommendations."
            "run_structured_agent",
            side_effect=RuntimeError("offline"),
        ):
            result = recommend_library_tracks("calm mood", items, model="model", limit=1)
        self.assertEqual([item.item.title for item in result], ["Calm One"])


if __name__ == "__main__":
    unittest.main()
