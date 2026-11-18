from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from unittest.mock import Mock
from urllib.error import URLError

from youtube_audio_video_downloader.metadata import id3_tagger


def test_parallel_tracks_download_shared_artwork_once(monkeypatch):
    tagger = id3_tagger.MetadataTagger()
    start = Barrier(8)
    all_waiting = Event()
    count_lock = Lock()
    waiter_count = 0

    class PendingEvent(Event):
        def wait(self, timeout=None):
            nonlocal waiter_count
            with count_lock:
                waiter_count += 1
                if waiter_count == 7:
                    all_waiting.set()
            return super().wait(timeout)

    monkeypatch.setattr(id3_tagger, "Event", PendingEvent)

    def fetch(url):
        assert all_waiting.wait(5), "same-URL callers did not overlap"
        return b"image", "image/jpeg"

    fetch_mock = Mock(side_effect=fetch)
    monkeypatch.setattr(tagger, "_fetch_album_art", fetch_mock)

    def request_art(_):
        start.wait(timeout=5)
        return tagger._download_album_art("https://art/cover.jpg")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(request_art, range(8)))
    assert results == [(b"image", "image/jpeg")] * 8
    fetch_mock.assert_called_once()
    assert not tagger._art_inflight


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


def test_different_artwork_urls_fetch_in_parallel(monkeypatch):
    tagger = id3_tagger.MetadataTagger()
    fetching = Barrier(4)

    def fetch(url):
        fetching.wait(timeout=5)
        return url.encode(), "image/jpeg"

    monkeypatch.setattr(tagger, "_fetch_album_art", fetch)
    urls = [f"https://art/{index}.jpg" for index in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(tagger._download_album_art, urls))
    assert [result[0] for result in results] == [url.encode() for url in urls]
    assert not tagger._art_inflight


def test_empty_artwork_response_is_a_failed_download(monkeypatch):
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = b""
    monkeypatch.setattr(id3_tagger, "urlopen", Mock(return_value=response))
    assert id3_tagger.MetadataTagger()._download_album_art("https://art/empty.jpg") is None
