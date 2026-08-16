from __future__ import annotations

from unittest.mock import patch

from youtube_audio_video_downloader.services.ai.metadata_agent import MetadataAgentDecision
from youtube_audio_video_downloader.services.metadata.metadata_verifier import verify_metadata


LOCAL = {
    "title": "O My Love",
    "artists": "Kunal Ganjawala, Shreya Ghoshal",
}


def _agent(metadata: dict[str, str], *, sources=("wikipedia", "catalog")):
    return MetadataAgentDecision(
        "apply", metadata, 0.96, "two sources identify one recording", sources
    )


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_rejects_partial_artist_catalog_match_before_agent(mock_agent):
    wiki = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": "Kunal Ganjawala, Shreya Ghoshal",
        "year": "2010",
    }
    wrong_catalog = {
        "title": "O My Love",
        "album": "Raaz 3D",
        "artists": "Sonu Nigam, Shreya Ghoshal",
        "year": "2012",
        "album_art": "https://example.test/raaz.jpg",
    }
    mock_agent.return_value = _agent(wiki, sources=("wikipedia",))

    result = verify_metadata(LOCAL, wiki, wrong_catalog, model="qwen2.5:7b")

    assert result.action == "apply"
    assert result.metadata["album"] == "Amanush"
    assert result.album_art == ""
    assert any(item.startswith("catalog:") for item in result.rejected_sources)
    passed_catalog = mock_agent.call_args.args[2]
    assert passed_catalog == {}


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_rejects_non_exact_title_source(mock_agent):
    catalog = {
        "title": "On My Love",
        "album": "Wrong Album",
        "artists": LOCAL["artists"],
        "year": "2012",
    }
    mock_agent.return_value = MetadataAgentDecision(
        "review", {}, 0.2, "no compatible evidence", ()
    )

    result = verify_metadata(LOCAL, {}, catalog, model="qwen2.5:7b")

    assert result.action == "review"
    assert any("title conflicts" in item for item in result.rejected_sources)
    assert mock_agent.call_args.args[2] == {}


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_accepts_narrow_oh_to_o_catalog_title_alias(mock_agent):
    evidence = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2010",
    }
    mock_agent.return_value = _agent(evidence, sources=("catalog",))

    result = verify_metadata(LOCAL, {}, evidence, model="qwen2.5:7b")

    assert result.action == "apply"
    assert result.metadata["album"] == "Amanush"


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_never_combines_incompatible_album_year_identities(mock_agent):
    wiki = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2010",
    }
    catalog = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2012",
        "album_art": "https://example.test/reissue.jpg",
    }
    mock_agent.return_value = _agent(
        {
            "title": "O My Love",
            "album": "Amanush",
            "artists": LOCAL["artists"],
            "year": "2010",
        }
    )

    result = verify_metadata(LOCAL, wiki, catalog, model="qwen2.5:7b")

    assert result.action == "apply"
    assert result.metadata["year"] == "2010"
    assert result.album_art == ""


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_compatible_catalog_artwork_is_attached_to_selected_identity(mock_agent):
    wiki = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2010",
    }
    catalog = {**wiki, "album_art": "https://example.test/amanush.jpg"}
    mock_agent.return_value = _agent(wiki)

    result = verify_metadata(LOCAL, wiki, catalog, model="qwen2.5:7b")

    assert result.action == "apply"
    assert result.album_art == "https://example.test/amanush.jpg"


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_agent_unavailability_uses_one_exact_static_internet_identity(mock_agent):
    evidence = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2010",
        "album_art": "https://example.test/amanush.jpg",
    }
    mock_agent.return_value = MetadataAgentDecision(
        "review", evidence, 0.0, "Agent unavailable: providers offline", ("catalog",)
    )

    result = verify_metadata(LOCAL, {}, evidence, model="qwen2.5:7b")

    assert result.action == "apply"
    assert result.metadata["album"] == "Amanush"
    assert result.album_art == "https://example.test/amanush.jpg"
    assert "AI provider chain unavailable" in result.reason


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_configured_agent_is_called_even_when_all_sources_are_rejected(mock_agent):
    wrong = {
        "title": "Different Song",
        "album": "Wrong Album",
        "artists": "Another Singer",
    }
    mock_agent.return_value = MetadataAgentDecision(
        "review", {}, 0.0, "No evidence", ()
    )

    result = verify_metadata(LOCAL, wrong, wrong, model="qwen2.5:7b")

    assert result.action == "review"
    mock_agent.assert_called_once()
    assert mock_agent.call_args.args[1] == {}
    assert mock_agent.call_args.args[2] == {}


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_agent_cannot_invent_cross_source_metadata(mock_agent):
    wiki = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2010",
    }
    mock_agent.return_value = _agent({**wiki, "language": "German"}, sources=("wikipedia",))

    result = verify_metadata(LOCAL, wiki, {}, model="qwen2.5:7b")

    assert result.action == "review"
    assert "incompatible evidence" in result.reason


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_agent_apply_must_preserve_full_known_artist_set(mock_agent):
    evidence = {
        "title": "O My Love",
        "album": "Amanush",
        "artists": LOCAL["artists"],
        "year": "2010",
    }
    mock_agent.return_value = _agent(
        {**evidence, "artists": "Shreya Ghoshal"}, sources=("wikipedia",)
    )

    result = verify_metadata(LOCAL, evidence, {}, model="qwen2.5:7b")

    assert result.action == "review"
    assert "full known artist set" in result.reason


@patch("youtube_audio_video_downloader.services.metadata.metadata_verifier.adjudicate_metadata")
def test_existing_album_rejects_matching_track_from_a_compilation(mock_agent):
    local = {
        "title": "Tu Hi Disda",
        "album": "Bhoot Bangla (2026)",
        "artists": "Arijit Singh, Nikhita Gandhi",
        "year": "2026",
    }
    compilation = {
        "title": "Tu Hi Disda",
        "album": "Love on Repeat",
        "artists": "Pritam, Arijit Singh, Nikhita Gandhi, Kumaar",
        "year": "2026",
        "album_art": "https://example.test/love-on-repeat.jpg",
    }
    mock_agent.return_value = MetadataAgentDecision(
        "review", {}, 0.0, "No compatible external identity", ()
    )

    result = verify_metadata(local, {}, compilation, model="configured-model")

    assert result.action == "review"
    assert any("protected existing album" in item for item in result.rejected_sources)
    assert mock_agent.call_args.args[2] == {}


def test_deterministic_serpapi_evidence_can_fill_missing_album() -> None:
    local = {"title": "Jonaki", "artists": "Papon"}
    serpapi = {
        "title": "Jonaki",
        "album": "Lorai",
        "artists": "Papon",
        "year": "2014",
    }

    result = verify_metadata(
        local, {}, {}, serpapi=serpapi, model=""
    )

    assert result.action == "apply"
    assert result.metadata["album"] == "Lorai"
    assert result.metadata["year"] == "2014"
    assert result.sources == ("serpapi",)
