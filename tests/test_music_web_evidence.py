"""Bounded semantic music evidence tests."""

from __future__ import annotations

from unittest.mock import patch

from youtube_audio_video_downloader.services.music_web_evidence import (
    find_music_web_evidence,
)


def test_returns_bounded_title_and_excerpt_without_local_context() -> None:
    rows = [
        {"title": "Dance Ka Bhoot", "body": "An upbeat Hindi dance track."},
        {"title": "Review", "body": "High-energy Bollywood music."},
    ]
    with patch(
        "youtube_audio_video_downloader.services.music_web_evidence.DDGS"
    ) as search:
        search.return_value.text.return_value = rows
        evidence = find_music_web_evidence(
            "Dance Ka Bhoot",
            "Arijit Singh",
            ("Hindi", "dance", "upbeat"),
        )

    assert "upbeat Hindi dance track" in evidence
    assert "High-energy Bollywood" in evidence
    query = search.return_value.text.call_args.args[0]
    assert "Dance Ka Bhoot Arijit Singh" in query
    assert "Hindi dance upbeat" in query
    assert len(evidence) <= 1800


def test_search_failure_is_an_empty_optional_evidence_result() -> None:
    with patch(
        "youtube_audio_video_downloader.services.music_web_evidence.DDGS",
        side_effect=RuntimeError("offline"),
    ):
        assert find_music_web_evidence("Song", "Artist", ("dance",)) == ""
