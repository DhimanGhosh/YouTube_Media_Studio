from __future__ import annotations

import pytest

from youtube_audio_video_downloader.gui.ai_usage import operation_ai_usage


@pytest.mark.parametrize(
    ("operation", "params", "purpose"),
    [
        ("audio", {"agentic_model": "qwen", "auto_enrich_downloads": True}, "Post-download"),
        ("video", {"agentic_model": "qwen"}, "Post-download"),
        ("album", {"agentic_model": "qwen"}, "Post-download"),
        ("jukebox", {"agentic_model": "qwen"}, "Post-download"),
        ("album_metadata_enricher", {"agentic_model": "qwen"}, "Track metadata"),
        ("album_consolidator", {"agentic_model": "qwen"}, "Pre-move"),
        ("search_song", {"model": "qwen"}, "Search intent"),
        ("enrich_song", {"model": "qwen"}, "Selected-track"),
    ],
)
def test_discloses_every_ai_operation(operation, params, purpose):
    usage = operation_ai_usage(operation, params)

    assert usage.active
    assert usage.model == "qwen"
    assert purpose.casefold() in usage.purpose.casefold()
    assert usage.badge_text.startswith("AI ON")


def test_discloses_preflight_for_deterministic_and_enrichment_disabled_operations():
    deterministic = operation_ai_usage("audio_trimmer", {"agentic_model": "qwen"})
    disabled = operation_ai_usage(
        "audio", {"agentic_model": "qwen", "auto_enrich_downloads": False}
    )

    assert deterministic.active
    assert "preflight" in deterministic.badge_text.casefold()
    assert disabled.active
    assert "preflight only" in disabled.badge_text.casefold()


def test_no_global_model_is_truthfully_disclosed():
    usage = operation_ai_usage("audio_trimmer", {})

    assert not usage.active
    assert "no model configured" in usage.badge_text


def test_per_tool_ai_off_discloses_internet_only_mode():
    usage = operation_ai_usage(
        "album_metadata_enricher",
        {"ai_enabled": False, "agentic_model": "qwen"},
    )

    assert not usage.active
    assert usage.model == ""
    assert "internet" in usage.purpose.casefold()


def test_global_model_enables_ai_preflight_for_every_workspace_operation():
    operations = {
        "audio", "video", "album", "jukebox", "track_reorder",
        "audio_trimmer", "redownload", "edit_media", "album_consolidator",
        "album_metadata_enricher", "duplicate_links", "format_artists",
        "parse_tracks", "search_song", "enrich_song",
    }

    for operation in operations:
        usage = operation_ai_usage(
            operation,
            {"agentic_model": "qwen", "model": "qwen"},
        )
        assert usage.active, operation
        assert usage.model == "qwen", operation
