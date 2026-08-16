"""Persistent fast-path index for files proven complete by Album Enricher."""

from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from pathlib import Path

from youtube_audio_video_downloader.services.ai.ai_provider import (
    NVIDIA_API_KEY_ENV,
    OLLAMA_MODEL_ENV,
)


def verification_policy_key(
    agentic_model: object, *, internet_only: bool = False
) -> str:
    """Return the tracker namespace for the verifier that approved a file."""

    model = str(agentic_model or "").strip()
    if not model:
        return "internet-v1" if internet_only else "legacy"
    provider = "nvidia-chain" if os.environ.get(NVIDIA_API_KEY_ENV, "").strip() else "ollama"
    fallback = os.environ.get(OLLAMA_MODEL_ENV, "").strip().casefold()
    return f"agentic-v2:{provider}:{model.casefold()}:{fallback}"


class MetadataCompletionTracker:
    """Skip unchanged completed files without reopening their media metadata."""

    # v14 rechecks files accepted before deterministic internet verification
    # became mandatory, including cached albums with an incorrect release year.
    # v13 protected populated albums and year-qualified
    # album folders became protected from storefront compilation matches.
    # This repairs cached files that older builds changed to collections such
    # as "Love on Repeat" instead of silently skipping them as complete.
    RULE_VERSION = 14
    _AUDIO_EXTENSIONS = frozenset(
        {".aac", ".aif", ".aiff", ".ape", ".flac", ".m4a", ".m4b", ".mp3",
         ".oga", ".ogg", ".opus", ".wav", ".wma", ".wv"}
    )

    def __init__(self, storage_path: str | Path | None) -> None:
        self.storage_path = Path(storage_path).expanduser() if storage_path else None
        self._records = self._load()

    def is_complete(self, path: Path, verification_policy: str = "legacy") -> bool:
        identity = self._identity(path)
        if identity is None:
            return False
        key = self._key(path)
        direct = self._records.get(key)
        if (
            direct
            and int(direct.get("rule_version", 0) or 0) == self.RULE_VERSION
            and direct.get("verification_policy", "legacy") == verification_policy
            and self._same_identity(direct, identity)
            and direct.get("folder_signature") == identity["folder_signature"]
        ):
            if not _essential_tags_complete(path):
                self._records.pop(key, None)
                self.save()
                return False
            direct["checked_at"] = time.time()
            return True
        # Recognize a completed file after a rename or move. Only transfer an
        # identity when its old path disappeared, avoiding false matches with a copy.
        for old_key, record in list(self._records.items()):
            if not self._same_identity(record, identity):
                continue
            if int(record.get("rule_version", 0) or 0) != self.RULE_VERSION:
                continue
            if record.get("verification_policy", "legacy") != verification_policy:
                continue
            old_path = Path(str(record.get("path") or ""))
            if old_path.exists():
                continue
            if not _essential_tags_complete(path):
                self._records.pop(old_key, None)
                self.save()
                return False
            self._records.pop(old_key, None)
            record["path"] = str(path.resolve())
            record.update(identity)
            record["checked_at"] = time.time()
            self._records[key] = record
            self.save()
            return True
        return False

    def mark_complete(
        self,
        paths: list[Path] | tuple[Path, ...],
        verification_policy: str = "legacy",
    ) -> None:
        now = time.time()
        for path in paths:
            identity = self._identity(path)
            if identity is None:
                continue
            self._records[self._key(path)] = {
                "path": str(path.resolve()),
                **identity,
                "rule_version": self.RULE_VERSION,
                "verification_policy": verification_policy,
                "checked_at": now,
            }
        self._trim()
        self.save()

    def save(self) -> None:
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.storage_path.with_name(
            f".{self.storage_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps({"version": 1, "files": self._records}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary, self.storage_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _load(self) -> dict[str, dict[str, object]]:
        if self.storage_path is None or not self.storage_path.is_file():
            return {}
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        files = payload.get("files", {}) if isinstance(payload, dict) else {}
        return {
            str(key): dict(value)
            for key, value in files.items()
            if isinstance(value, dict)
        } if isinstance(files, dict) else {}

    def _trim(self) -> None:
        if len(self._records) <= 50000:
            return
        oldest = sorted(
            self._records,
            key=lambda key: float(self._records[key].get("checked_at", 0) or 0),
        )
        for key in oldest[: len(self._records) - 50000]:
            self._records.pop(key, None)

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path.resolve()))

    @staticmethod
    def _identity(path: Path) -> dict[str, int | str] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return {
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "folder_signature": MetadataCompletionTracker._folder_signature(path.parent),
        }

    @staticmethod
    def _folder_signature(folder: Path) -> str:
        parts: list[str] = []
        try:
            children = sorted(folder.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            return ""
        for child in children:
            if not child.is_file() or child.suffix.lower() not in MetadataCompletionTracker._AUDIO_EXTENSIONS:
                continue
            try:
                stat = child.stat()
            except OSError:
                continue
            parts.append(f"{child.name.casefold()}\0{stat.st_size}\0{stat.st_mtime_ns}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _same_identity(
        record: dict[str, object], identity: dict[str, int | str]
    ) -> bool:
        try:
            return (
                int(record.get("size", -1)) == identity["size"]
                and int(record.get("mtime_ns", -1)) == identity["mtime_ns"]
            )
        except (TypeError, ValueError):
            return False


def _essential_tags_complete(path: Path) -> bool:
    """Confirm that a cached file still has the metadata the cache promises."""

    from youtube_audio_video_downloader.services.media.media_metadata import (
        read_media_metadata,
    )

    try:
        metadata = read_media_metadata(path)
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    return all(
        (
            str(metadata.title or "").strip(),
            str(metadata.album or "").strip(),
            str(metadata.artists or "").strip(),
            str(metadata.year or "").strip(),
            metadata.artwork_present,
        )
    )
