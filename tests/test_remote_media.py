"""Tests for authenticated LAN Media Library access."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from youtube_audio_video_downloader.services.remote_media import (
    RemoteMediaServer,
    media_id,
)


def request_json(
    url: str,
    *,
    payload: dict[str, object] | None = None,
    token: str = "",
) -> tuple[int, dict[str, object]]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())
    with response:
        return response.status, json.loads(response.read())


def test_remote_server_requires_pin_and_dispatches_authenticated_actions() -> None:
    received: list[dict[str, object]] = []
    dispatched = threading.Event()

    def callback(action: dict[str, object]) -> None:
        received.append(action)
        dispatched.set()

    server = RemoteMediaServer(callback, host="127.0.0.1", port=0, html="<h1>Remote</h1>")
    server.update_state({"tracks": [{"title": "Song"}]}, {})
    server.start()
    base = f"http://127.0.0.1:{server.port}"
    try:
        with urllib.request.urlopen(base, timeout=3) as response:
            assert b"Remote" in response.read()

        status, _payload = request_json(f"{base}/api/state")
        assert status == 401
        wrong_pin = "000000" if server.pin != "000000" else "999999"
        status, _payload = request_json(
            f"{base}/api/login", payload={"pin": wrong_pin}
        )
        assert status == 401

        status, login = request_json(
            f"{base}/api/login", payload={"pin": server.pin}
        )
        assert status == 200
        token = str(login["token"])
        status, state = request_json(f"{base}/api/state", token=token)
        assert status == 200
        assert state["tracks"] == [{"title": "Song"}]
        assert int(state["revision"]) == 1

        status, response = request_json(
            f"{base}/api/action",
            payload={"type": "create_playlist", "name": "Phone"},
            token=token,
        )
        assert status == 202
        assert response == {"accepted": True}
        assert dispatched.wait(2)
        assert received == [{"type": "create_playlist", "name": "Phone"}]
    finally:
        server.stop()


def test_remote_server_streams_only_allowlisted_media_with_range_support() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "song.mp3"
        path.write_bytes(b"0123456789")
        identifier = media_id(str(path))
        server = RemoteMediaServer(lambda _action: None, host="127.0.0.1", port=0, html="")
        server.update_state({}, {identifier: str(path)})
        server.start()
        base = f"http://127.0.0.1:{server.port}"
        try:
            unauthorized = urllib.request.Request(f"{base}/media/{identifier}")
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(unauthorized, timeout=3)
            assert error.value.code == 401

            media_request = urllib.request.Request(
                f"{base}/media/{identifier}?token={server.token}",
                headers={"Range": "bytes=2-5"},
            )
            with urllib.request.urlopen(media_request, timeout=3) as response:
                assert response.status == 206
                assert response.headers["Content-Range"] == "bytes 2-5/10"
                assert response.read() == b"2345"

            missing = urllib.request.Request(
                f"{base}/media/not-allowlisted?token={server.token}"
            )
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(missing, timeout=3)
            assert error.value.code == 404
        finally:
            server.stop()


def test_media_id_is_stable_without_disclosing_the_path() -> None:
    path = "C:/Private Music/Artist/Song.mp3"
    identifier = media_id(path)
    assert identifier == media_id(path)
    assert len(identifier) == 24
    assert "Private" not in identifier


def test_pin_authentication_rate_limits_repeated_failures() -> None:
    server = RemoteMediaServer(lambda _action: None, port=0, html="")
    wrong_pin = "000000" if server.pin != "000000" else "999999"

    for _attempt in range(5):
        assert server.authenticate_pin("192.168.1.20", wrong_pin) == (False, False)

    assert server.authenticate_pin("192.168.1.20", wrong_pin) == (False, True)
    assert server.authenticate_pin("192.168.1.20", server.pin) == (False, True)
    assert server.authenticate_pin("192.168.1.21", server.pin) == (True, False)


def test_stopping_remote_access_closes_active_client_connections() -> None:
    server = RemoteMediaServer(
        lambda _action: None,
        host="127.0.0.1",
        port=0,
        html="",
    )
    server_side, client_side = socket.socketpair()
    server.start()
    try:
        server._register_connection(server_side)
        server.stop()
        client_side.settimeout(1)
        assert client_side.recv(1) == b""
    finally:
        server.stop()
        server_side.close()
        client_side.close()
