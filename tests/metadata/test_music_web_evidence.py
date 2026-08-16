"""Bounded semantic music evidence tests."""

from __future__ import annotations

from unittest.mock import patch

from youtube_audio_video_downloader.services.metadata.music_web_evidence import (
    find_music_web_evidence,
)


def test_returns_bounded_title_and_excerpt_without_local_context() -> None:
    evidence_text = (
        '[{"title":"Dance Ka Bhoot","body":"An upbeat Hindi dance track."},'
        '{"title":"Review","body":"High-energy Bollywood music."}]'
    )
    with patch(
        "youtube_audio_video_downloader.services.metadata.music_web_evidence.DuckDuckGoTools"
    ) as search:
        search.return_value.duckduckgo_search.return_value = evidence_text
        evidence = find_music_web_evidence(
            "Dance Ka Bhoot",
            "Arijit Singh",
            ("Hindi", "dance", "upbeat"),
        )

    assert "upbeat Hindi dance track" in evidence
    assert "High-energy Bollywood" not in evidence
    query = search.return_value.duckduckgo_search.call_args.args[0]
    assert '"Dance Ka Bhoot" Arijit Singh' in query
    assert "Hindi dance upbeat" in query
    assert len(evidence) <= 900


def test_discards_search_results_for_a_different_song() -> None:
    evidence_text = (
        '[{"title":"Another Bengali Song","body":"A Bengali track."},'
        '{"title":"Target Song","body":"A Hindi language song."}]'
    )
    with patch(
        "youtube_audio_video_downloader.services.metadata.music_web_evidence.DuckDuckGoTools"
    ) as search:
        search.return_value.duckduckgo_search.return_value = evidence_text
        evidence = find_music_web_evidence(
            "Target Song", "Kumar Sanu", ("Bengali",)
        )

    assert "Another Bengali Song" not in evidence
    assert "Hindi language song" in evidence


def test_search_failure_is_an_empty_optional_evidence_result() -> None:
    with patch(
        "youtube_audio_video_downloader.services.metadata.music_web_evidence.DuckDuckGoTools",
        side_effect=RuntimeError("offline"),
    ):
        assert find_music_web_evidence("Song", "Artist", ("dance",)) == ""
