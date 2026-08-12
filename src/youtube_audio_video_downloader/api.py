"""Public Python API for the workflows available in YouTube Media Studio.

The desktop application and this module intentionally share the same operation
executor.  A wheel consumer therefore receives the same validation, defaults,
cancellation behavior, and result summaries as a GUI worker.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Mapping

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import (
    SUPPORTED_OPERATIONS,
    OperationSummary,
    execute_operation,
)


class Operation(StrEnum):
    """Stable names for every background workflow exposed by the GUI."""

    AUDIO = "audio"
    VIDEO = "video"
    ALBUM = "album"
    JUKEBOX = "jukebox"
    TRACK_REORDER = "track_reorder"
    AUDIO_TRIMMER = "audio_trimmer"
    REDOWNLOAD = "redownload"
    EDIT_MEDIA = "edit_media"
    EDIT_ALBUM = "edit_album"
    ALBUM_CONSOLIDATOR = "album_consolidator"
    ALBUM_METADATA_ENRICHER = "album_metadata_enricher"
    DUPLICATE_LINKS = "duplicate_links"
    FORMAT_ARTISTS = "format_artists"
    PARSE_TRACKS = "parse_tracks"
    SEARCH_SONG = "search_song"
    ENRICH_SONG = "enrich_song"


def run_operation(
    operation: Operation | str,
    params: Mapping[str, Any] | None = None,
    *,
    cancellation_token: CancellationToken | None = None,
    **overrides: Any,
) -> OperationSummary:
    """Run one GUI workflow synchronously and return its structured summary.

    ``params`` accepts the same values as the corresponding desktop form.
    Keyword arguments are merged last, making small programmatic overrides easy.
    Supply a shared :class:`CancellationToken` when another thread must be able
    to stop a long-running workflow cooperatively.
    """

    name = str(operation).strip()
    if name not in SUPPORTED_OPERATIONS:
        choices = ", ".join(SUPPORTED_OPERATIONS)
        raise ValueError(f"Unsupported operation: {name}. Choose one of: {choices}")
    payload = dict(params or {})
    payload.update(overrides)
    return execute_operation(name, payload, cancellation_token or CancellationToken())


class MediaStudio:
    """Convenient reusable client for the complete GUI operation surface."""

    def __init__(
        self,
        *,
        defaults: Mapping[str, Any] | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> None:
        self.defaults = dict(defaults or {})
        self.cancellation_token = cancellation_token or CancellationToken()

    @property
    def operations(self) -> tuple[str, ...]:
        """Return all operation names accepted by :meth:`run`."""

        return SUPPORTED_OPERATIONS

    def cancel(self) -> None:
        """Request cooperative cancellation of the current operation."""

        self.cancellation_token.cancel()

    def reset_cancellation(self) -> None:
        """Allow this client to be reused after a cancellation request."""

        self.cancellation_token.reset()

    def run(
        self,
        operation: Operation | str,
        params: Mapping[str, Any] | None = None,
        **overrides: Any,
    ) -> OperationSummary:
        """Run an operation with client defaults and call-specific values."""

        payload = dict(self.defaults)
        payload.update(params or {})
        payload.update(overrides)
        return run_operation(
            operation,
            payload,
            cancellation_token=self.cancellation_token,
        )

    def audio(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> OperationSummary:
        return self.run(Operation.AUDIO, params, **kwargs)

    def video(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> OperationSummary:
        return self.run(Operation.VIDEO, params, **kwargs)

    def album(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> OperationSummary:
        return self.run(Operation.ALBUM, params, **kwargs)

    def jukebox(self, params: Mapping[str, Any] | None = None, **kwargs: Any) -> OperationSummary:
        return self.run(Operation.JUKEBOX, params, **kwargs)

    def track_reorder(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.TRACK_REORDER, params, **kwargs)

    def audio_trimmer(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.AUDIO_TRIMMER, params, **kwargs)

    def redownload(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.REDOWNLOAD, params, **kwargs)

    def edit_media(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.EDIT_MEDIA, params, **kwargs)

    def edit_album(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.EDIT_ALBUM, params, **kwargs)

    def album_consolidator(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.ALBUM_CONSOLIDATOR, params, **kwargs)

    def album_metadata_enricher(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.ALBUM_METADATA_ENRICHER, params, **kwargs)

    def duplicate_links(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.DUPLICATE_LINKS, params, **kwargs)

    def format_artists(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.FORMAT_ARTISTS, params, **kwargs)

    def parse_tracks(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.PARSE_TRACKS, params, **kwargs)

    def search_song(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.SEARCH_SONG, params, **kwargs)

    def enrich_song(
        self, params: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> OperationSummary:
        return self.run(Operation.ENRICH_SONG, params, **kwargs)


__all__ = [
    "CancellationToken",
    "MediaStudio",
    "Operation",
    "OperationSummary",
    "SUPPORTED_OPERATIONS",
    "run_operation",
]
