"""Validated parallel byte ranges for progressive HTTP media, without extra binaries."""

from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from yt_dlp.downloader import get_suitable_downloader
from yt_dlp.downloader.http import HttpFD
from yt_dlp.networking import Request
from yt_dlp.networking.exceptions import RequestError

from youtube_audio_video_downloader.core.exceptions import UserCancelledError


class RangeUnsupported(Exception):
    """The server cannot safely serve independently validated byte ranges."""


class ParallelHttpFD(HttpFD):
    def real_download(self, filename, info_dict):
        count = max(1, min(32, int(self.params.get("concurrent_fragment_downloads", 1))))
        if count == 1:
            return super().real_download(filename, info_dict)
        try:
            return self._ranges(filename, info_dict, count)
        except UserCancelledError:
            raise
        except (RangeUnsupported, OSError, RequestError) as exc:
            self.to_screen(f"Parallel transfer unavailable ({exc}); retrying with one connection")
            info_dict["parallel_fallback"] = str(exc)
            return super().real_download(filename, info_dict)

    def _ranges(self, filename, info, count):
        headers = dict(info.get("http_headers") or {})
        headers["Accept-Encoding"] = "identity"

        def request(start, end, validator=""):
            request_headers = {**headers, "Range": f"bytes={start}-{end}"}
            if validator:
                request_headers["If-Range"] = validator
            return self.ydl.urlopen(Request(info["url"], headers=request_headers,
                                           extensions={"timeout": 15}))

        def validate(response, start, end, total=None):
            match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)",
                                 response.headers.get("Content-Range", ""))
            if response.status != 206 or not match:
                raise RangeUnsupported("server does not honor byte ranges")
            first, last, size = map(int, match.groups())
            if (first, last) != (start, end) or (total is not None and size != total):
                raise RangeUnsupported("server returned a different byte range")
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise RangeUnsupported("server compressed a byte range")
            return size

        with request(0, 0) as probe:
            total = validate(probe, 0, 0)
            validator = probe.headers.get("ETag", "")
            if validator.startswith("W/"):
                validator = ""
            validator = validator or probe.headers.get("Last-Modified", "")
            if len(probe.read(2)) != 1:
                raise RangeUnsupported("invalid probe length")
        # Avoid connection overhead for tiny files.
        count = min(count, total // (256 * 1024))
        if count < 2:
            raise RangeUnsupported("file is smaller than 512 KB")
        target = Path(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = threading.Lock()
        abort = threading.Event()
        sizes = [0] * count
        lengths = [(total * (i + 1) // count) - (total * i // count) for i in range(count)]
        started = time.monotonic()

        def progress(state="downloading"):
            downloaded = sum(sizes)
            elapsed = max(0.001, time.monotonic() - started)
            self._hook_progress({
                "status": state, "filename": filename, "downloaded_bytes": downloaded,
                "total_bytes": total, "speed": downloaded / elapsed,
                "eta": (total - downloaded) * elapsed / max(1, downloaded),
                "parallel_ranges": count,
                "range_progress": [round(100 * size / length) for size, length in zip(sizes, lengths)],
            }, info)

        with TemporaryDirectory(prefix=".yms-ranges-", dir=target.parent) as directory:
            parts = [Path(directory) / str(i) for i in range(count)]

            def transfer(index):
                start = total * index // count
                end = total * (index + 1) // count - 1
                try:
                    for attempt in range(3):
                        try:
                            with request(start + sizes[index], end, validator) as response:
                                validate(response, start + sizes[index], end, total)
                                current_validator = (response.headers.get("ETag", "")
                                                     if validator.startswith('"') else
                                                     response.headers.get("Last-Modified", ""))
                                if validator and current_validator and current_validator != validator:
                                    raise RangeUnsupported("source changed during transfer")
                                with parts[index].open("ab") as output:
                                    while sizes[index] < lengths[index]:
                                        if abort.is_set():
                                            return
                                        chunk = response.read(min(64 * 1024, lengths[index] - sizes[index]))
                                        if not chunk:
                                            raise OSError("incomplete byte range")
                                        output.write(chunk)
                                        with lock:
                                            sizes[index] += len(chunk)
                                            progress()
                                    if response.read(1):
                                        raise RangeUnsupported("oversized byte range")
                            return
                        except (OSError, RequestError):
                            if attempt == 2:
                                raise
                except BaseException:
                    abort.set()
                    raise

            with ThreadPoolExecutor(max_workers=count, thread_name_prefix="yt-range") as pool:
                futures = [pool.submit(transfer, i) for i in range(count)]
                for future in futures:
                    future.result()
            # Join inside the private staging directory. Never expose a partial result.
            assembled = Path(directory) / "joined"
            with assembled.open("wb") as output:
                for part in parts:
                    with part.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            output.write(chunk)
                            progress()
            os.replace(assembled, target)
        progress("finished")
        return True


def enable_parallel_http(downloader) -> None:
    """Customize this yt-dlp instance only; keep extraction and postprocessing native."""
    native_dl = downloader.dl

    def dl(name, info, subtitle=False, test=False):
        if (not subtitle and not test and name != "-"
                and get_suitable_downloader(info, downloader.params) is HttpFD
                and not info.get("is_live") and not info.get("impersonate")
                and not downloader.params.get("ratelimit")):
            fd = ParallelHttpFD(downloader, downloader.params)
            for hook in downloader._progress_hooks:
                fd.add_progress_hook(hook)
            copied = downloader._copy_infodict(info)
            if copied.get("http_headers") is None:
                copied["http_headers"] = downloader._calc_headers(copied)
            return fd.download(name, copied, subtitle)
        return native_dl(name, info, subtitle=subtitle, test=test)

    downloader.dl = dl
