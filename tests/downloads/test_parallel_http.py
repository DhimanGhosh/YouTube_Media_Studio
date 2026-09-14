from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from yt_dlp import YoutubeDL

from youtube_audio_video_downloader.core.exceptions import UserCancelledError
from youtube_audio_video_downloader.services.downloads.parallel_http import enable_parallel_http


@contextmanager
def media_server(mode="ranges"):
    data = bytes(range(256)) * 8192
    state = {"active": 0, "peak": 0, "ranges": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            start, end = 0, len(data) - 1
            value = self.headers.get("Range")
            partial = bool(value) and mode != "ignore"
            if partial:
                start_text, end_text = value.removeprefix("bytes=").split("-")
                start, end = int(start_text), int(end_text or end)
            self.send_response(206 if partial else 200)
            if partial:
                actual = start + 1 if mode == "wrong" and end > 0 else start
                self.send_header("Content-Range", f"bytes {actual}-{end}/{len(data)}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("ETag", '"stable"')
            self.end_headers()
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
                state["ranges"] += int(partial and end > 0)
            try:
                for offset in range(start, end + 1, 16384):
                    self.wfile.write(data[offset:min(end + 1, offset + 16384)])
                    if end > 0:
                        time.sleep(0.002)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                with lock:
                    state["active"] -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/media", data, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("mode", ["ranges", "ignore", "wrong"])
def test_parallel_ranges_produce_exact_file_or_safe_fallback(tmp_path, mode):
    events = []
    with media_server(mode) as (url, expected, state), YoutubeDL({
        "quiet": True, "concurrent_fragment_downloads": 4,
        "progress_hooks": [events.append],
    }) as downloader:
        enable_parallel_http(downloader)
        target = tmp_path / "output.bin"
        result, _ = downloader.dl(str(target), {"url": url, "protocol": "http"})
        assert result
        assert target.read_bytes() == expected
        if mode == "ranges":
            assert state["peak"] >= 2
            assert state["ranges"] == 4
            assert events[-1]["parallel_ranges"] == 4
            assert events[-1]["range_progress"] == [100] * 4
        else:
            assert "parallel_fallback" in events[-1]["info_dict"]
        assert sorted(p.name for p in tmp_path.iterdir()) == ["output.bin"]


def test_parallel_cancellation_cleans_staging_and_never_publishes_partial_file(tmp_path):
    def cancel(event):
        if event.get("downloaded_bytes", 0):
            raise UserCancelledError("Cancelled")

    with media_server() as (url, _expected, _state), YoutubeDL({
        "quiet": True, "concurrent_fragment_downloads": 4, "progress_hooks": [cancel],
    }) as downloader:
        enable_parallel_http(downloader)
        with pytest.raises(UserCancelledError):
            downloader.dl(str(tmp_path / "output.bin"), {"url": url, "protocol": "http"})
        assert list(tmp_path.iterdir()) == []
