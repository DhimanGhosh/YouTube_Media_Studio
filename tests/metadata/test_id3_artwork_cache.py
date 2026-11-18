from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from urllib.error import URLError

from youtube_audio_video_downloader.metadata import id3_tagger


def test_parallel_tracks_download_shared_artwork_once(monkeypatch):
    tagger = id3_tagger.MetadataTagger()
    fetch = Mock(return_value=(b"image", "image/jpeg"))
    monkeypatch.setattr(tagger, "_fetch_album_art", fetch)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(tagger._download_album_art, ["https://art/cover.jpg"] * 20))
    assert results == [(b"image", "image/jpeg")] * 20
    fetch.assert_called_once()


def test_failed_artwork_is_retried_after_short_cooldown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(id3_tagger.time, "monotonic", lambda: clock[0])
    opener = Mock(side_effect=URLError("offline"))
    monkeypatch.setattr(id3_tagger, "urlopen", opener)
    tagger = id3_tagger.MetadataTagger()
    assert tagger._download_album_art("https://art/fail.jpg") is None
    assert tagger._download_album_art("https://art/fail.jpg") is None
    assert opener.call_count == 1
    clock[0] += 31
    tagger._download_album_art("https://art/fail.jpg")
    assert opener.call_count == 2


def test_artwork_cache_is_bounded_and_successes_expire(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(id3_tagger.time, "monotonic", lambda: clock[0])
    tagger = id3_tagger.MetadataTagger()
    fetch = Mock(return_value=(b"image", "image/jpeg"))
    monkeypatch.setattr(tagger, "_fetch_album_art", fetch)
    for index in range(12):
        tagger._download_album_art(f"https://art/{index}.jpg")
    assert len(tagger._art_cache) == 8
    tagger._download_album_art("https://art/11.jpg")
    assert fetch.call_count == 12
    clock[0] += 301
    tagger._download_album_art("https://art/11.jpg")
    assert fetch.call_count == 13


def test_tagging_embeds_player_art_and_preserves_it_on_network_failure(tmp_path, monkeypatch):
    import shutil
    import subprocess
    import pytest
    from mutagen.id3 import ID3
    from youtube_audio_video_downloader.domain.models import ParsedSongMetadata, Song
    from youtube_audio_video_downloader.services.media.media_library import artwork_bytes

    if not shutil.which("ffmpeg"):
        pytest.skip("needs FFmpeg")
    path = tmp_path / "track.mp3"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=0.1", "-codec:a", "libmp3lame", str(path),
    ], check=True)
    song = Song("1", "", "track", ParsedSongMetadata("Title", "Album", ["Artist"]),
                album_art="https://art/cover.jpg")
    tagger = id3_tagger.MetadataTagger()
    image = b"\xff\xd8\xff\xd9"
    monkeypatch.setattr(tagger, "_download_album_art", lambda url: (image, "image/jpeg"))
    tagger.tag_mp3(path, song)
    assert artwork_bytes(path) == image
    assert len(ID3(path).getall("APIC")) == 1
    monkeypatch.setattr(tagger, "_download_album_art", lambda url: None)
    tagger.tag_mp3(path, song)
    assert artwork_bytes(path) == image
