"""Portable trust must add bundled roots without disabling TLS verification."""

import ssl
from unittest.mock import Mock

import pytest

from youtube_audio_video_downloader.core import network


@pytest.fixture(autouse=True)
def clear_context():
    network.https_context.cache_clear()
    yield
    network.https_context.cache_clear()


def test_bundled_roots_supplement_system_trust(monkeypatch):
    context = Mock()
    factory = Mock(return_value=context)
    monkeypatch.setattr(network.ssl, "create_default_context", factory)
    monkeypatch.setattr(network.certifi, "where", lambda: "/bundle/cacert.pem")
    assert network.https_context() is context
    assert network.https_context() is context
    factory.assert_called_once_with()
    context.load_verify_locations.assert_called_once_with(cafile="/bundle/cacert.pem")


def test_real_context_requires_valid_certificate_and_hostname():
    context = network.https_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.cert_store_stats()["x509_ca"] > 0


def test_urlopen_preserves_timeout_body_and_explicit_context(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(network, "_urlopen", opener)
    context = ssl.create_default_context()
    network.urlopen("https://example.com", b"payload", timeout=7, context=context)
    opener.assert_called_once_with(
        "https://example.com", data=b"payload", timeout=7, context=context,
    )


def test_default_context_also_applies_to_http_redirects(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(network, "_urlopen", opener)
    network.urlopen("http://example.com", timeout=2)
    assert opener.call_args.kwargs["context"] is network.https_context()
