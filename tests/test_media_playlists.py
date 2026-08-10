"""Tests for persistent Media Library playlist behavior."""

from youtube_audio_video_downloader.services.media_playlists import (
    add_playlist_paths,
    decode_playlists,
    encode_playlists,
)


def test_playlist_payload_round_trip_preserves_order_and_duplicate_links() -> None:
    playlists = {"Road trip": ["C:/A.mp3", "C:/A.mp3", "C:/B.mp3"]}

    assert decode_playlists(encode_playlists(playlists)) == playlists


def test_skip_duplicates_adds_only_new_album_tracks() -> None:
    result = add_playlist_paths(
        ["C:/Album/One.mp3"],
        ["c:/album/one.mp3", "C:/Album/Two.mp3", "C:/Album/Three.mp3"],
        skip_duplicates=True,
    )

    assert result.duplicates == 1
    assert result.added == 2
    assert result.paths == [
        "C:/Album/One.mp3",
        "C:/Album/Two.mp3",
        "C:/Album/Three.mp3",
    ]


def test_add_anyway_keeps_duplicate_track_links() -> None:
    result = add_playlist_paths(
        ["C:/Song.mp3"], ["c:/song.mp3"], skip_duplicates=False
    )

    assert result.duplicates == 1
    assert result.added == 1
    assert result.paths == ["C:/Song.mp3", "c:/song.mp3"]


def test_invalid_playlist_payload_is_ignored_and_logged(caplog) -> None:
    with caplog.at_level("WARNING"):
        assert decode_playlists("not-json") == {}
        assert decode_playlists('["not", "a", "mapping"]') == {}

    assert "Could not decode saved Media Library playlists" in caplog.text
    assert "not a mapping" in caplog.text
