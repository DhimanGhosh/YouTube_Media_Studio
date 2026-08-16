"""Tests for permanent video crop and aspect transforms."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from youtube_audio_video_downloader.services.media.video_transformer import (
    build_video_transform_filter,
    permanently_transform_video,
)


def test_filter_bakes_crop_before_output_aspect() -> None:
    result = build_video_transform_filter("1:1", "16:9")

    assert result.startswith("crop=")
    assert ",scale=" in result
    assert result.endswith(",setsar=1")


@pytest.mark.parametrize(
    ("crop", "aspect"),
    (("Default", "Default"), ("3:2", "Default"), ("Default", "3:2")),
)
def test_filter_rejects_empty_or_unknown_edits(crop: str, aspect: str) -> None:
    with pytest.raises(ValueError):
        build_video_transform_filter(crop, aspect)


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="needs FFmpeg and FFprobe",
)
def test_permanent_transform_replaces_video_with_baked_dimensions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "display.mp4"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "color=c=blue:s=320x240:d=0.25", "-c:v", "libx264", "-pix_fmt",
            "yuv420p", str(source),
        ],
        check=True,
    )

    result = permanently_transform_video(
        source,
        crop_ratio="1:1",
        aspect_ratio="16:9",
    )

    assert result == source
    assert source.is_file()
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
            "stream=width,height,sample_aspect_ratio", "-of", "json", str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert stream["width"] == 426
    assert stream["height"] == 240
    assert stream["sample_aspect_ratio"] == "1:1"
