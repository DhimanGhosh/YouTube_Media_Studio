"""Grounding and workflow tests for agentic local-library suggestions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from youtube_audio_video_downloader.services.library_recommendations import (
    MAX_EVIDENCE_LOOKUPS,
    MAX_SEMANTIC_CANDIDATES,
    playlist_taste_search_query,
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
    def test_playlist_names_and_tracks_ground_taste_ranking_without_paths(self) -> None:
        unrelated = track("C:/private/unrelated.mp3", "Other", "Other Artist")
        related = track("C:/private/related.mp3", "New Favourite", "Liked Artist")
        seed = track("C:/private/seed.mp3", "Beloved Song", "Liked Artist")
        items = [unrelated, related, seed]
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
                            for index in range(3)
                        ]
                    }
                ),
                response({"ids": []}),
            ],
        ) as agent:
            result = recommend_library_tracks(
                "songs",
                items,
                model="model",
                playlists={"Slow Bengali": [seed.path]},
            )

        self.assertEqual(result[0].item, related)
        self.assertIn("saved playlist taste", result[0].reason)
        planner_context = agent.call_args_list[0].kwargs["input_data"]
        curator_context = agent.call_args_list[2].kwargs["input_data"]
        self.assertEqual(planner_context["playlist_taste_profile"][0]["name"], "Slow Bengali")
        self.assertEqual(
            planner_context["playlist_taste_profile"][0]["tracks"][0]["title"],
            "Beloved Song",
        )
        self.assertEqual(
            curator_context["playlist_taste_profile"],
            planner_context["playlist_taste_profile"],
        )
        self.assertNotIn("C:/private", str(planner_context))

    def test_youtube_taste_query_contains_bounded_playlist_examples(self) -> None:
        seed = track("C:/private/seed.mp3", "Smriti", "Bhoomi")

        query = playlist_taste_search_query(
            "more songs for a quiet evening",
            [seed],
            {"Slow Bengali": [seed.path]},
        )

        self.assertIn("Slow Bengali: Smriti by Bhoomi", query)
        self.assertNotIn(seed.path, query)

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
            recommend_library_tracks("songs", items, model="global-model")

        supplied = chat_mock.call_args_list[1].kwargs["input_data"]["catalog"]
        self.assertEqual(
            chat_mock.call_args_list[1].kwargs["requested_model"], "global-model"
        )
        self.assertEqual(len(supplied), MAX_EVIDENCE_LOOKUPS)
        self.assertNotIn("path", supplied[0])
        self.assertEqual(
            set(supplied[0]), {"id", "title", "artists", "album", "year", "type"},
        )
        curator_catalog = chat_mock.call_args_list[2].kwargs["input_data"]["catalog"]
        self.assertEqual(len(curator_catalog), MAX_EVIDENCE_LOOKUPS)

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

    def test_planner_cannot_drop_an_explicit_language_constraint(self) -> None:
        items = [
            track("hindi.mp3", "Chot Dil Pe Lagi", "Kumar Sanu"),
            track("bengali.mp3", "Duti Pakhi Duti Teere", "Kumar Sanu"),
        ]
        semantic = response(
            {
                "matches": [
                    {
                        "id": index,
                        "matches": True,
                        "confidence": 0.95,
                        "matched_filters": ["Bengali"],
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
                    plan(artists=["Kumar Sanu"]),
                    semantic,
                    response({"ids": [0]}),
                ],
            ) as agent_mock,
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                side_effect=lambda candidates, _filters: {
                    index: {
                        "language": (
                            "Bengali"
                            if item.title == "Duti Pakhi Duti Teere"
                            else "Hindi"
                        )
                    }
                    for index, item in enumerate(candidates)
                },
            ),
        ):
            result = recommend_library_tracks(
                "kumar sanu bengali", items, model="model"
            )

        self.assertEqual([value.item.title for value in result], ["Duti Pakhi Duti Teere"])
        verifier_input = agent_mock.call_args_list[1].kwargs["input_data"]
        self.assertEqual(verifier_input["filters"], ("bengali",))
        self.assertIn("bengali", result[0].reason)

    def test_screenshot_query_rejects_hindi_results_and_duplicate_recordings(self) -> None:
        items = [
            track("duti-one.mp3", "Duti Pakhi Duti Teere", "Kumar Sanu"),
            track(
                "khel.mp3",
                "Aa Khel Khelen Hum",
                "Asha Bhosle, Kumar Sanu, Kishore Kumar",
            ),
            track("chander.mp3", "Chander Eto Alo", "Kumar Sanu"),
            track("deewana.mp3", "Dil Hai Mera Deewana", "Kumar Sanu"),
            track("duti-two.mp3", "Duti Pakhi Duti Teere", "Kumar Sanu"),
            track("raat.mp3", "Ei Raat Bhalobashar", "Kumar Sanu"),
        ]
        evidence_by_title = {
            "Duti Pakhi Duti Teere": {"language": "Bengali"},
            "Aa Khel Khelen Hum": {
                "language": "Hindi",
                "web_search_excerpts": "A search result also mentions Bengali music.",
            },
            "Chander Eto Alo": {"language": "Bengali"},
            "Dil Hai Mera Deewana": {
                "language": "Hindi",
                "web_search_excerpts": "A search result also mentions Bengali music.",
            },
            "Ei Raat Bhalobashar": {"language": "Bengali"},
        }

        def agent_response(**kwargs: object) -> SimpleNamespace:
            name = str(kwargs["name"]).casefold()
            if name == "library query planner":
                return plan(artists=["Kumar Sanu"])
            input_data = kwargs["input_data"]
            assert isinstance(input_data, dict)
            catalog = input_data["catalog"]
            assert isinstance(catalog, list)
            if name == "library semantic verifier":
                return response(
                    {
                        "matches": [
                            {
                                "id": row["id"],
                                "matches": True,
                                "confidence": 0.99,
                                "matched_filters": ["Bengali"],
                            }
                            for row in catalog
                        ]
                    }
                )
            return response({"ids": [row["id"] for row in catalog]})

        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=agent_response,
            ) as agent_mock,
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                side_effect=lambda candidates, _filters: {
                    index: evidence_by_title[item.title]
                    for index, item in enumerate(candidates)
                },
            ),
        ):
            result = recommend_library_tracks(
                "kumar sanu bengali", items, model="model", limit=10
            )

        self.assertEqual(
            [value.item.title for value in result],
            ["Chander Eto Alo", "Duti Pakhi Duti Teere", "Ei Raat Bhalobashar"],
        )
        verifier_input = agent_mock.call_args_list[1].kwargs["input_data"]
        self.assertEqual(verifier_input["filters"], ("bengali",))
        self.assertEqual(len(verifier_input["catalog"]), 5)

    def test_planner_outage_still_enforces_recovered_constraints(self) -> None:
        items = [
            track("hindi.mp3", "Hindi Track", "Kumar Sanu"),
            track("bengali.mp3", "Bengali Track", "Kumar Sanu"),
        ]
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    RuntimeError("planner offline"),
                    RuntimeError("verifier offline"),
                    response({"ids": [0]}),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                side_effect=lambda candidates, _filters: {
                    index: {"language": "Bengali" if "Bengali" in item.title else "Hindi"}
                    for index, item in enumerate(candidates)
                },
            ),
        ):
            result = recommend_library_tracks(
                "kumar sanu bengali", items, model="model"
            )

        self.assertEqual([value.item.title for value in result], ["Bengali Track"])

    def test_recovered_constraints_respect_verifier_schema_limit(self) -> None:
        items = [track("song.mp3", "Song", "Kumar Sanu")]
        verbose_constraints = " ".join(f"trait{index}" for index in range(20))
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(artists=["Kumar Sanu"]),
                    response({"matches": []}),
                ],
            ) as agent_mock,
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={},
            ),
        ):
            result = recommend_library_tracks(
                f"kumar sanu {verbose_constraints}", items, model="model"
            )

        self.assertEqual(result, [])
        verifier_filters = agent_mock.call_args_list[1].kwargs["input_data"]["filters"]
        self.assertEqual(len(verifier_filters), 12)

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

    def test_agent_can_map_sad_to_an_exact_melancholic_evidence_phrase(self) -> None:
        items = [track("song.mp3", "Bojhabo Ki Kore", "Arijit Singh")]
        semantic = response(
            {
                "matches": [
                    {
                        "id": 0,
                        "matches": True,
                        "confidence": 0.95,
                        "matched_filters": ["Bengali", "sad"],
                        "evidence_support": [
                            {"filter": "sad", "phrase": "melancholic ballad"}
                        ],
                    }
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
                        languages=["Bengali"],
                        semantic_filters=["sad"],
                    ),
                    semantic,
                    response(
                        {
                            "judgments": [
                                {
                                    "id": 0,
                                    "supports": True,
                                    "confidence": 0.95,
                                }
                            ]
                        }
                    ),
                    response({"ids": [0]}),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={
                    0: {
                        "language": "Bengali",
                        "web_search_excerpts": "A melancholic ballad about heartbreak.",
                    }
                },
            ),
        ):
            result = recommend_library_tracks(
                "arijit sad bengali", items, model="model"
            )

        self.assertEqual([value.item.title for value in result], ["Bojhabo Ki Kore"])

    def test_agent_semantic_phrase_must_exist_in_supplied_evidence(self) -> None:
        items = [track("song.mp3", "Upbeat Song", "Arijit Singh")]
        semantic = response(
            {
                "matches": [
                    {
                        "id": 0,
                        "matches": True,
                        "confidence": 0.95,
                        "matched_filters": ["Bengali", "sad"],
                        "evidence_support": [
                            {"filter": "sad", "phrase": "melancholic ballad"}
                        ],
                    }
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
                        languages=["Bengali"],
                        semantic_filters=["sad"],
                    ),
                    semantic,
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={
                    0: {
                        "language": "Bengali",
                        "web_search_excerpts": "An energetic upbeat Bengali song.",
                    }
                },
            ),
        ):
            result = recommend_library_tracks(
                "arijit sad bengali", items, model="model"
            )

        self.assertEqual(result, [])

    def test_generic_cited_phrase_cannot_entail_a_sad_mood(self) -> None:
        items = [track("song.mp3", "Aajke Raatey", "Arijit Singh")]
        semantic = response(
            {
                "matches": [
                    {
                        "id": 0,
                        "matches": True,
                        "confidence": 0.95,
                        "matched_filters": ["Bengali", "sad"],
                        "evidence_support": [
                            {"filter": "sad", "phrase": "Bengali song"}
                        ],
                    }
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
                        languages=["Bengali"],
                        semantic_filters=["sad"],
                    ),
                    semantic,
                    response(
                        {
                            "judgments": [
                                {"id": 0, "supports": False, "confidence": 0.99}
                            ]
                        }
                    ),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={
                    0: {
                        "language": "Bengali",
                        "web_search_excerpts": "A Bengali song from Bismillah.",
                    }
                },
            ),
        ):
            result = recommend_library_tracks(
                "arijit sad bengali", items, model="model"
            )

        self.assertEqual(result, [])

    def test_empty_ranker_output_preserves_the_verified_candidate_set(self) -> None:
        items = [track("song.mp3", "Bengali Sad Song", "Arijit Singh")]
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=[
                    plan(
                        artists=["Arijit Singh"],
                        languages=["Bengali"],
                        semantic_filters=["sad"],
                    ),
                    response(
                        {
                            "matches": [
                                {
                                    "id": 0,
                                    "matches": True,
                                    "confidence": 0.95,
                                    "matched_filters": ["Bengali", "sad"],
                                }
                            ]
                        }
                    ),
                    response({"ids": []}),
                ],
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={
                    0: {
                        "language": "Bengali",
                        "web_search_excerpts": "A sad Bengali song.",
                    }
                },
            ),
        ):
            result = recommend_library_tracks(
                "arijit sad bengali", items, model="model"
            )

        self.assertEqual([value.item.title for value in result], ["Bengali Sad Song"])

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
        with (
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "run_structured_agent",
                side_effect=RuntimeError("offline"),
            ),
            patch(
                "youtube_audio_video_downloader.services.library_recommendations."
                "_collect_catalog_evidence",
                return_value={0: {"web_search_excerpts": "A calm track."}},
            ),
        ):
            result = recommend_library_tracks("calm mood", items, model="model", limit=1)
        self.assertEqual([item.item.title for item in result], ["Calm One"])


if __name__ == "__main__":
    unittest.main()
