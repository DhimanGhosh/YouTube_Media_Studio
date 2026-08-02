from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from youtube_audio_video_downloader.services import serpapi_metadata


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _jonaki_payload() -> dict[str, object]:
    return {
        "organic_results": [
            {
                "title": "Jonaki - LORAI | Papon",
                "source": "YouTube · Asha Audio",
                "snippet": (
                    "Song - Jonaki Singer - Papon Film - LORAI "
                    "Music on - Asha Audio Release year - 2014"
                ),
            },
            {
                "title": "Jonaki by Papon",
                "source": "Shazam",
                "snippet": (
                    "Jonaki was released on December 1, 2014 by Asha Audio "
                    "as part of the album Lorai (Original Motion Picture Soundtrack)."
                ),
            },
        ]
    }


def test_extracts_agreeing_exact_google_results() -> None:
    result = serpapi_metadata.extract_serpapi_song_metadata(
        _jonaki_payload(), "Jonaki", "Papon"
    )

    assert result == {
        "title": "Jonaki",
        "album": "Lorai",
        "artists": "Papon",
        "year": "2014",
        "source": "SerpApi Google Search",
        "evidence_count": "2",
    }


def test_rejects_one_unstructured_snippet() -> None:
    payload = {
        "organic_results": [
            {
                "title": "Jonaki - LORAI | Papon",
                "snippet": "Song - Jonaki Singer - Papon Film - LORAI",
            }
        ]
    }

    assert serpapi_metadata.extract_serpapi_song_metadata(
        payload, "Jonaki", "Papon"
    ) == {}


def test_accepts_exact_structured_knowledge_graph() -> None:
    payload = {
        "knowledge_graph": {
            "title": "Jonaki",
            "type": "Song by Papon",
            "album": "Lorai (Original Motion Picture Soundtrack)",
            "release_date": "2014",
        }
    }

    result = serpapi_metadata.extract_serpapi_song_metadata(
        payload, "Jonaki", "Papon"
    )

    assert result["album"] == "Lorai"
    assert result["year"] == "2014"


def test_rejects_results_for_a_different_artist() -> None:
    payload = _jonaki_payload()

    assert serpapi_metadata.extract_serpapi_song_metadata(
        payload, "Jonaki", "Different Artist"
    ) == {}


def test_search_uses_saved_key_without_logging_it(capsys, monkeypatch) -> None:
    monkeypatch.setenv(serpapi_metadata.SERPAPI_API_KEY_ENV, "private-serp-key")
    captured_url = ""

    def open_request(request, timeout):
        nonlocal captured_url
        captured_url = request.full_url
        assert timeout == 7
        return _Response(_jonaki_payload())

    with patch.object(serpapi_metadata, "urlopen", side_effect=open_request):
        result = serpapi_metadata.find_serpapi_song_metadata(
            "Jonaki", "Papon", timeout=7
        )

    query = parse_qs(urlparse(captured_url).query)
    assert query["api_key"] == ["private-serp-key"]
    assert query["engine"] == ["google"]
    assert query["q"] == ["Jonaki - Papon"]
    assert result["album"] == "Lorai"
    assert "private-serp-key" not in capsys.readouterr().out


def test_accepts_nested_movie_card_from_google_song_panel() -> None:
    payload = {
        "knowledge_graph": {
            "title": "Jonaki",
            "type": "Song by Papon · 2014",
            "movies": [{"name": "Lorai"}],
        }
    }

    result = serpapi_metadata.extract_serpapi_song_metadata(
        payload, "Jonaki", "Papon"
    )

    assert result["album"] == "Lorai"


def test_collaboration_matches_when_google_credits_one_requested_artist() -> None:
    payload = {
        "knowledge_graph": {
            "title": "O Megh",
            "type": "Song by Papon",
            "movie": {"name": "Aami Shudhu Cheyechi Tomay"},
            "release_date": "2014",
        }
    }

    result = serpapi_metadata.extract_serpapi_song_metadata(
        payload, "O Megh", "Papon, Shantanu Moitra"
    )

    assert result["album"] == "Aami Shudhu Cheyechi Tomay"
    assert result["year"] == "2014"


def test_request_failure_never_prints_key(capsys, monkeypatch) -> None:
    monkeypatch.setenv(serpapi_metadata.SERPAPI_API_KEY_ENV, "private-serp-key")
    with patch.object(serpapi_metadata, "urlopen", side_effect=OSError("failed URL")):
        assert serpapi_metadata.find_serpapi_song_metadata("Jonaki", "Papon") == {}

    output = capsys.readouterr().out
    assert "SERPAPI-UNAVAILABLE" in output
    assert "private-serp-key" not in output


def test_album_art_uses_safe_square_original(capsys, monkeypatch) -> None:
    monkeypatch.setenv(serpapi_metadata.SERPAPI_API_KEY_ENV, "private-serp-key")
    payload = {
        "images_results": [
            {
                "original": "https://example.test/landscape.jpg",
                "original_width": 1200,
                "original_height": 800,
            },
            {
                "original": "https://example.test/cover.jpg",
                "original_width": 1200,
                "original_height": 1200,
                "unsafe": False,
            },
        ]
    }

    with patch.object(serpapi_metadata, "urlopen", return_value=_Response(payload)) as opened:
        result = serpapi_metadata.find_serpapi_album_art("Lorai", "2014")

    assert result == "https://example.test/cover.jpg"
    query = parse_qs(urlparse(opened.call_args.args[0].full_url).query)
    assert query["engine"] == ["google_images"]
    assert query["imgar"] == ["s"]
    assert "private-serp-key" not in capsys.readouterr().out


def test_album_art_rejects_unsafe_or_non_square_images(monkeypatch) -> None:
    monkeypatch.setenv(serpapi_metadata.SERPAPI_API_KEY_ENV, "private-serp-key")
    payload = {
        "images_results": [
            {
                "original": "https://example.test/unsafe.jpg",
                "original_width": 900,
                "original_height": 900,
                "unsafe": True,
            },
            {
                "original": "https://example.test/wide.jpg",
                "original_width": 900,
                "original_height": 600,
            },
        ]
    }

    with patch.object(serpapi_metadata, "urlopen", return_value=_Response(payload)):
        assert serpapi_metadata.find_serpapi_album_art("Lorai", "2014") == ""
