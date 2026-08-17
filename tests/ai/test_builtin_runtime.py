"""Security and lifecycle tests for the managed CPU AI assets."""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from youtube_audio_video_downloader.services.ai import builtin_runtime


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


@pytest.fixture(autouse=True)
def reset_runtime_failure_circuit(monkeypatch):
    monkeypatch.setattr(builtin_runtime, "_UNAVAILABLE_UNTIL", 0.0)
    monkeypatch.setattr(builtin_runtime, "_LAST_ERROR", "")


def test_runtime_manifest_supports_released_desktop_architectures() -> None:
    assert "win-cpu-x64" in builtin_runtime.runtime_artifact("Windows", "AMD64").filename
    assert "ubuntu-arm64" in builtin_runtime.runtime_artifact("Linux", "aarch64").filename
    assert "macos-arm64" in builtin_runtime.runtime_artifact("Darwin", "arm64").filename


def test_runtime_manifest_rejects_an_unsupported_cpu() -> None:
    with pytest.raises(RuntimeError, match="does not support"):
        builtin_runtime.runtime_artifact("plan9", "mips")


def test_download_is_atomic_and_checksum_verified(tmp_path: Path) -> None:
    payload = b"verified local model"
    target = tmp_path / "model.gguf"
    digest = hashlib.sha256(payload).hexdigest()
    with patch.object(builtin_runtime, "urlopen", return_value=Response(payload)):
        builtin_runtime._download_verified(
            "https://example.test/model", target, digest, len(payload), "test model"
        )
    assert target.read_bytes() == payload
    assert not target.with_suffix(".gguf.part").exists()


def test_bad_download_is_deleted(tmp_path: Path) -> None:
    target = tmp_path / "model.gguf"
    with (
        patch.object(builtin_runtime, "urlopen", return_value=Response(b"bad")),
        pytest.raises(RuntimeError, match="failed size or SHA-256"),
    ):
        builtin_runtime._download_verified(
            "https://example.test/model", target, "0" * 64, 3, "test model"
        )
    assert not target.exists()
    assert not target.with_suffix(".gguf.part").exists()


def test_zip_path_traversal_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape/llama-server", b"unsafe")
    with pytest.raises(RuntimeError, match="unsafe path"):
        builtin_runtime._safe_extract(archive, tmp_path / "extract")
    assert not (tmp_path / "escape").exists()


def test_thread_budget_keeps_capacity_for_the_application(monkeypatch) -> None:
    monkeypatch.setattr(builtin_runtime.os, "cpu_count", lambda: 16)
    assert builtin_runtime._cpu_threads() == 8
    monkeypatch.setattr(builtin_runtime.os, "cpu_count", lambda: 2)
    assert builtin_runtime._cpu_threads() == 1


def test_setup_failure_has_a_retry_cooldown() -> None:
    with patch.object(
        builtin_runtime, "_ensure_builtin_server", side_effect=RuntimeError("offline")
    ) as setup:
        with pytest.raises(RuntimeError, match="offline"):
            builtin_runtime.ensure_builtin_server()
        with pytest.raises(RuntimeError, match="Recent built-in AI setup failed"):
            builtin_runtime.ensure_builtin_server()
    setup.assert_called_once_with()
