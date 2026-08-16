"""Authenticated LAN web access for the running Media Library."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import secrets
import socket
import threading
import time
from collections.abc import Callable, Mapping
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ActionCallback = Callable[[dict[str, object]], None]


def media_id(path: str) -> str:
    """Return a stable, path-hiding identifier for one indexed media file."""

    return hashlib.sha256(path.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def lan_addresses(port: int) -> list[str]:
    """Return likely phone-reachable HTTP addresses for this computer."""

    addresses: set[str] = set()
    try:
        for result in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(result[4][0])
            if not address.startswith("127.") and address != "0.0.0.0":
                addresses.add(address)
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = str(probe.getsockname()[0])
            if not address.startswith("127."):
                addresses.add(address)
        finally:
            probe.close()
    except OSError:
        pass
    return [f"http://{address}:{port}" for address in sorted(addresses)]


class RemoteMediaServer:
    """Small authenticated HTTP server backed by a thread-safe UI snapshot."""

    def __init__(
        self,
        action_callback: ActionCallback,
        *,
        host: str = "0.0.0.0",
        port: int = 8765,
        html: str,
    ) -> None:
        self.action_callback = action_callback
        self.host = host
        self.requested_port = max(0, min(65_535, int(port)))
        self.html = html.encode("utf-8")
        self.pin = f"{secrets.randbelow(1_000_000):06d}"
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._state: dict[str, object] = {
            "revision": 0,
            "tracks": [],
            "albums": [],
            "playlists": [],
            "recommendations": [],
            "curator": {"status": "AI idle", "request": ""},
            "playback": {},
        }
        self._media_paths: dict[str, str] = {}
        self._login_failures: dict[str, list[float]] = {}
        self._connections: set[socket.socket] = set()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else 0

    @property
    def urls(self) -> list[str]:
        return lan_addresses(self.port) if self.port else []

    def start(self) -> None:
        if self._server is not None:
            return
        handler = self._handler_type()
        try:
            server = ThreadingHTTPServer((self.host, self.requested_port), handler)
        except OSError:
            if not self.requested_port:
                raise
            server = ThreadingHTTPServer((self.host, 0), handler)
        server.daemon_threads = True
        server.remote_owner = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="MediaLibraryLAN",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            self._close_connections()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def update_state(
        self,
        state: Mapping[str, object],
        media_paths: Mapping[str, str],
    ) -> bool:
        """Publish a new state only when its meaningful contents changed."""

        candidate = dict(state)
        candidate.pop("revision", None)
        with self._lock:
            current = dict(self._state)
            revision = int(current.pop("revision", 0))
            if candidate == current and dict(media_paths) == self._media_paths:
                return False
            candidate["revision"] = revision + 1
            self._state = candidate
            self._media_paths = dict(media_paths)
            return True

    def state(self) -> dict[str, object]:
        with self._lock:
            return json.loads(json.dumps(self._state))

    def resolve_media(self, identifier: str) -> Path | None:
        with self._lock:
            raw = self._media_paths.get(identifier)
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_file() else None

    def dispatch(self, action: dict[str, object]) -> None:
        self.action_callback(action)

    def _register_connection(self, connection: socket.socket) -> None:
        with self._lock:
            self._connections.add(connection)

    def _unregister_connection(self, connection: socket.socket) -> None:
        with self._lock:
            self._connections.discard(connection)

    def _close_connections(self) -> None:
        with self._lock:
            connections = list(self._connections)
            self._connections.clear()
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass

    def authenticate_pin(self, address: str, candidate: str) -> tuple[bool, bool]:
        """Validate a PIN and rate-limit repeated failures from one client."""

        now = time.monotonic()
        with self._lock:
            failures = [
                timestamp
                for timestamp in self._login_failures.get(address, [])
                if now - timestamp < 60
            ]
            if len(failures) >= 5:
                self._login_failures[address] = failures
                return False, True
            if secrets.compare_digest(candidate, self.pin):
                self._login_failures.pop(address, None)
                return True, False
            failures.append(now)
            self._login_failures[address] = failures
            return False, False

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        class Handler(BaseHTTPRequestHandler):
            server_version = "YouTubeMediaStudioLAN/1"

            def setup(self) -> None:
                super().setup()
                self.owner._register_connection(self.connection)

            def finish(self) -> None:
                try:
                    super().finish()
                finally:
                    self.owner._unregister_connection(self.connection)

            @property
            def owner(self) -> RemoteMediaServer:
                return self.server.remote_owner  # type: ignore[attr-defined, no-any-return]

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/":
                    self._bytes(HTTPStatus.OK, self.owner.html, "text/html; charset=utf-8")
                    return
                if parsed.path == "/api/state":
                    if not self._authorized():
                        self._json(HTTPStatus.UNAUTHORIZED, {"error": "PIN required"})
                        return
                    self._json(HTTPStatus.OK, self.owner.state())
                    return
                if parsed.path.startswith("/media/"):
                    if not self._authorized():
                        self._json(HTTPStatus.UNAUTHORIZED, {"error": "PIN required"})
                        return
                    self._media(unquote(parsed.path.removeprefix("/media/")))
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

            def do_HEAD(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path.startswith("/media/") and self._authorized():
                    self._media(unquote(parsed.path.removeprefix("/media/")), head=True)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                payload = self._payload()
                if payload is None:
                    return
                if parsed.path == "/api/login":
                    valid, limited = self.owner.authenticate_pin(
                        self.client_address[0],
                        str(payload.get("pin", "")),
                    )
                    if valid:
                        self._json(HTTPStatus.OK, {"token": self.owner.token})
                    elif limited:
                        self._json(
                            HTTPStatus.TOO_MANY_REQUESTS,
                            {"error": "Too many attempts; wait one minute"},
                        )
                    else:
                        self._json(HTTPStatus.UNAUTHORIZED, {"error": "Incorrect PIN"})
                    return
                if parsed.path == "/api/action":
                    if not self._authorized():
                        self._json(HTTPStatus.UNAUTHORIZED, {"error": "PIN required"})
                        return
                    action = str(payload.get("type", ""))
                    if action not in {
                        "add_to_playlist",
                        "create_playlist",
                        "curate",
                        "play",
                        "queue",
                        "remove_playlist_positions",
                        "reorder_playlist",
                    }:
                        self._json(HTTPStatus.BAD_REQUEST, {"error": "Unknown action"})
                        return
                    self.owner.dispatch(payload)
                    self._json(HTTPStatus.ACCEPTED, {"accepted": True})
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

            def _payload(self) -> dict[str, object] | None:
                try:
                    length = min(65_536, max(0, int(self.headers.get("Content-Length", "0"))))
                    value = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(value, dict):
                        raise ValueError("Payload must be an object")
                    return value
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
                    return None

            def _authorized(self) -> bool:
                header = self.headers.get("Authorization", "")
                candidate = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
                if not candidate:
                    candidate = parse_qs(urlparse(self.path).query).get("token", [""])[0]
                return bool(candidate) and secrets.compare_digest(candidate, self.owner.token)

            def _json(self, status: HTTPStatus, payload: object) -> None:
                self._bytes(
                    status,
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    "application/json; charset=utf-8",
                )

            def _bytes(self, status: HTTPStatus, data: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)

            def _media(self, identifier: str, *, head: bool = False) -> None:
                path = self.owner.resolve_media(identifier)
                if path is None:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "Media unavailable"})
                    return
                size = path.stat().st_size
                start, end = 0, max(0, size - 1)
                partial = False
                range_header = self.headers.get("Range", "")
                if range_header.startswith("bytes="):
                    try:
                        first, last = range_header[6:].split("-", 1)
                        start = int(first) if first else 0
                        end = min(size - 1, int(last)) if last else size - 1
                        if start < 0 or start > end or start >= size:
                            raise ValueError
                        partial = True
                    except ValueError:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                length = end - start + 1
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.send_header("Cache-Control", "private, max-age=3600")
                if partial:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if head or self.command == "HEAD":
                    return
                try:
                    with path.open("rb") as handle:
                        handle.seek(start)
                        remaining = length
                        while remaining:
                            chunk = handle.read(min(1024 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        return Handler
