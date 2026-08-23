"""Local audio/video input coverage for album and jukebox splitters."""

from __future__ import annotations

import json

from youtube_audio_video_downloader.services.albums.album_splitter import (
    YouTubeAlbumSplitter,
)
from youtube_audio_video_downloader.services.albums.jukebox_splitter import (
    YouTubeJukeboxSplitter,
)


def test_album_json_resolves_relative_existing_source_file(tmp_path) -> None:
    source = tmp_path / "already-downloaded.webm"
    source.write_bytes(b"media")
    config = tmp_path / "album.json"
    config.write_text(
        json.dumps(
            {
                "Local album": {
                    "source_file": source.name,
                    "tracks": [{"One": {"start": "0:00", "end": "0:01"}}],
                }
            }
        ),
        encoding="utf-8",
    )

    jobs, _base = YouTubeAlbumSplitter._load_jobs(
        config, album_name=None, artists=None
    )

    assert jobs[0].ytb_link == str(source.resolve())
    resolved, info = YouTubeAlbumSplitter()._download_source_audio(
        jobs[0], tmp_path
    )
    assert resolved == source.resolve()
    assert info["_filename"] == str(source.resolve())


def test_jukebox_json_accepts_local_file_alias(tmp_path) -> None:
    source = tmp_path / "jukebox.mp4"
    source.write_bytes(b"media")
    config = tmp_path / "jukebox.json"
    config.write_text(
        json.dumps(
            {
                "Local jukebox": {
                    "local_file": source.name,
                    "tracks": [
                        {
                            "Song": {
                                "start": "0:00",
                                "end": "0:01",
                                "album": "Album",
                                "artists": "Artist",
                            }
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    jobs, _base = YouTubeJukeboxSplitter._load_jukebox_jobs(config)

    assert jobs[0].ytb_link == str(source.resolve())
