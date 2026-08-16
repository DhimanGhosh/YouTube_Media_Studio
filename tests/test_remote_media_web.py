"""Regression checks for the phone Media Library client."""

from __future__ import annotations

from pathlib import Path


REMOTE_MEDIA_HTML = Path(__file__).parents[1] / "assets" / "remote_media.html"


def test_disconnect_stops_and_unloads_phone_media() -> None:
    html = REMOTE_MEDIA_HTML.read_text(encoding="utf-8")

    assert "function showLogin(){ stopPhonePlayback();" in html
    assert "['audio','video'].forEach" in html
    assert "player.pause();" in html
    assert "player.removeAttribute('src');" in html
    assert "player.load();" in html
