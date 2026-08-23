from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from youtube_audio_video_downloader.gui.components.widgets import (  # noqa: E402
    DownloadProgressPanel,
)


def test_unknown_size_finished_download_is_authoritatively_complete() -> None:
    _app = QApplication.instance() or QApplication([])
    panel = DownloadProgressPanel()

    panel.update_download({
        "label": "Album",
        "status": "finished",
        "percent": 0,
        "downloaded": 12_000,
        "total": 0,
        "connections_configured": 8,
        "connections_used": 1,
        "fragmented": False,
    })

    assert panel.overall.maximum() == 1000
    assert panel.overall.value() == 1000
    assert panel.overall.format() == "Download complete"
    assert len(panel.connection_bars) == 1
    assert panel.connection_bars[0].value() == 100
    assert "ETA" not in panel.stats.text()


def test_fragmented_transfer_shows_configured_lanes_until_finished() -> None:
    _app = QApplication.instance() or QApplication([])
    panel = DownloadProgressPanel()

    panel.update_download({
        "status": "downloading",
        "percent": 50,
        "downloaded": 50,
        "total": 100,
        "connections_configured": 4,
        "connections_used": 2,
        "fragmented": True,
    })

    assert len(panel.connection_bars) == 4
    assert [bar.value() for bar in panel.connection_bars] == [50, 50, 0, 0]
