"""Verified HTTPS for source installs and frozen desktop applications."""

from __future__ import annotations

import ssl
from functools import lru_cache
from urllib.request import urlopen as _urlopen

import certifi


@lru_cache(maxsize=1)
def https_context() -> ssl.SSLContext:
    """Keep system/custom trust and add the CA bundle shipped with the app.

    Frozen macOS Python cannot rely on a build machine's OpenSSL CA paths.
    Loading certifi in addition to default roots keeps verification enabled
    while also respecting locally configured certificate authorities.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def urlopen(url, data=None, timeout=None, *, context=None):
    """Open a URL with portable trust roots, including across HTTPS redirects."""
    return _urlopen(
        url, data=data, timeout=timeout,
        context=context if context is not None else https_context(),
    )
