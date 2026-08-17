"""Managed zero-configuration CPU inference for the built-in AI provider.

The runtime and model are downloaded on first use into the application's per-user
data directory.  Both artifacts are pinned and checksum-verified; no executable or
model is accepted merely because a download completed.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import tarfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.error import URLError
from urllib.request import Request, urlopen

from youtube_audio_video_downloader.config.app_storage import resolve_data_directory


BUILTIN_PROVIDER_ID = "builtin"
BUILTIN_PROVIDER_LABEL = "Built-in CPU AI"
BUILTIN_MODEL_ID = "Qwen3-0.6B-Q8_0"
BUILTIN_MODEL_FILE = f"{BUILTIN_MODEL_ID}.gguf"
BUILTIN_MODEL_URL = (
    "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/main/"
    f"{BUILTIN_MODEL_FILE}"
)
BUILTIN_MODEL_SIZE = 639_446_688
BUILTIN_MODEL_SHA256 = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
LLAMA_CPP_BUILD = "b10453"
BUILTIN_CONTEXT_WINDOW = 8192
BUILTIN_DOWNLOAD_TIMEOUT_SECONDS = 120
BUILTIN_START_TIMEOUT_SECONDS = 120
DOWNLOAD_HEADROOM_BYTES = 256 * 1024 * 1024
BUILTIN_FAILURE_COOLDOWN_SECONDS = 60


@dataclass(frozen=True, slots=True)
class RuntimeArtifact:
    system: str
    machine: str
    filename: str
    size: int
    sha256: str

    @property
    def url(self) -> str:
        return (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            f"{LLAMA_CPP_BUILD}/{self.filename}"
        )


_RUNTIME_ARTIFACTS = {
    ("windows", "x86_64"): RuntimeArtifact(
        "windows", "x86_64", "llama-b10453-bin-win-cpu-x64.zip", 18_464_078,
        "70c07211d0027305f0be09cd755d79641ebb0bb646590ff3d498c66b22df29b0",
    ),
    ("windows", "arm64"): RuntimeArtifact(
        "windows", "arm64", "llama-b10453-bin-win-cpu-arm64.zip", 12_221_580,
        "a8b984d478700777d4671cf33eccfddae42c1fd871e78efd43fee090131eec1f",
    ),
    ("linux", "x86_64"): RuntimeArtifact(
        "linux", "x86_64", "llama-b10453-bin-ubuntu-x64.tar.gz", 16_645_691,
        "550eb155a09c3051c7add5becf6d0badc3a4c33416807985963036b27b859fb4",
    ),
    ("linux", "arm64"): RuntimeArtifact(
        "linux", "arm64", "llama-b10453-bin-ubuntu-arm64.tar.gz", 13_510_472,
        "b164e72dfb69c711275178e0d0fae54748042f039e4fe7386f1c0ea7019c109c",
    ),
    ("darwin", "arm64"): RuntimeArtifact(
        "darwin", "arm64", "llama-b10453-bin-macos-arm64.tar.gz", 11_072_482,
        "f1531b1c520f8b473d83352c5eec2f4f43bd0a54f9ca1366a6f202211cfbc098",
    ),
    ("darwin", "x86_64"): RuntimeArtifact(
        "darwin", "x86_64", "llama-b10453-bin-macos-x64.tar.gz", 11_379_413,
        "ac13f6f6c90c193765921bf52dd5ecf2a9d506ee9c3eadd2d6fd49ca7a5de25d",
    ),
}

_LOCK = threading.RLock()
_PROCESS: subprocess.Popen[bytes] | None = None
_BASE_URL = ""
_UNAVAILABLE_UNTIL = 0.0
_LAST_ERROR = ""


def builtin_ai_directory() -> Path:
    override = os.environ.get("YOUTUBE_MEDIA_STUDIO_BUILTIN_AI_DIR", "").strip()
    return Path(override).expanduser().resolve() if override else resolve_data_directory() / "ai"


def runtime_artifact(
    system: str | None = None, machine: str | None = None
) -> RuntimeArtifact:
    system_key = (system or platform.system()).strip().casefold()
    machine_key = (machine or platform.machine()).strip().casefold()
    machine_key = {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64"}.get(
        machine_key, machine_key
    )
    try:
        return _RUNTIME_ARTIFACTS[(system_key, machine_key)]
    except KeyError as exc:
        raise RuntimeError(
            f"Built-in CPU AI does not support {system_key}/{machine_key}. "
            "Select Ollama or a hosted provider in Global Settings."
        ) from exc


def builtin_status() -> dict[str, object]:
    root = builtin_ai_directory()
    try:
        runtime = _server_executable(root)
    except RuntimeError:
        runtime = None
    model = root / "models" / BUILTIN_MODEL_FILE
    with _LOCK:
        running = bool(_PROCESS is not None and _PROCESS.poll() is None)
    return {
        "provider": BUILTIN_PROVIDER_LABEL,
        "model": BUILTIN_MODEL_ID,
        "runtime_ready": bool(runtime and runtime.is_file()),
        "model_ready": model.is_file() and model.stat().st_size == BUILTIN_MODEL_SIZE,
        "running": running,
        "directory": str(root),
        "download_bytes": BUILTIN_MODEL_SIZE + runtime_artifact().size,
    }


def ensure_builtin_server() -> tuple[str, str]:
    """Start local inference while suppressing repeated downloads after one failure."""

    global _UNAVAILABLE_UNTIL, _LAST_ERROR
    with _LOCK:
        remaining = _UNAVAILABLE_UNTIL - time.monotonic()
        if remaining > 0:
            raise RuntimeError(
                f"Recent built-in AI setup failed; retry in {max(1, round(remaining))}s. "
                f"{_LAST_ERROR}"
            )
    try:
        result = _ensure_builtin_server()
    except Exception as exc:
        with _LOCK:
            _LAST_ERROR = " ".join(str(exc).split())[:300]
            _UNAVAILABLE_UNTIL = time.monotonic() + BUILTIN_FAILURE_COOLDOWN_SECONDS
        raise
    with _LOCK:
        _UNAVAILABLE_UNTIL = 0.0
        _LAST_ERROR = ""
    return result


def _ensure_builtin_server() -> tuple[str, str]:
    """Install missing assets, start the private CPU server, and return API identity."""

    global _PROCESS, _BASE_URL
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None and _server_ready(_BASE_URL):
            return _BASE_URL, BUILTIN_MODEL_ID
        stop_builtin_server()
        root = builtin_ai_directory()
        root.mkdir(parents=True, exist_ok=True)
        executable = _ensure_runtime(root)
        model = _ensure_model(root)
        port = _free_local_port()
        _BASE_URL = f"http://127.0.0.1:{port}/v1"
        threads = _cpu_threads()
        command = [
            str(executable),
            "--model", str(model),
            "--alias", BUILTIN_MODEL_ID,
            "--host", "127.0.0.1",
            "--port", str(port),
            "--ctx-size", str(BUILTIN_CONTEXT_WINDOW),
            "--threads", str(threads),
            "--threads-batch", str(threads),
            "--n-gpu-layers", "0",
            "--jinja",
            "--reasoning", "off",
            "--reasoning-budget", "0",
            "--chat-template-kwargs", '{"enable_thinking":false}',
            "--no-webui",
        ]
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        _PROCESS = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + BUILTIN_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if _PROCESS.poll() is not None:
                raise RuntimeError("The built-in CPU AI runtime stopped during startup.")
            if _server_ready(_BASE_URL):
                print(
                    f"[AI-PROVIDER] {BUILTIN_PROVIDER_LABEL} ready | "
                    f"model={BUILTIN_MODEL_ID} threads={threads}"
                )
                return _BASE_URL, BUILTIN_MODEL_ID
            time.sleep(0.25)
        stop_builtin_server()
        raise RuntimeError("The built-in CPU AI runtime did not become ready in time.")


def stop_builtin_server() -> None:
    global _PROCESS, _BASE_URL
    process, _PROCESS, _BASE_URL = _PROCESS, None, ""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def remove_builtin_assets() -> None:
    """Remove only managed AI assets; preference profiles and app data remain."""

    global _UNAVAILABLE_UNTIL, _LAST_ERROR
    with _LOCK:
        stop_builtin_server()
        _UNAVAILABLE_UNTIL = 0.0
        _LAST_ERROR = ""
        root = builtin_ai_directory()
        for child in (root / "runtime", root / "models", root / "downloads"):
            if child.exists():
                shutil.rmtree(child)


def _ensure_runtime(root: Path) -> Path:
    artifact = runtime_artifact()
    try:
        installed = _server_executable(root)
        marker = json.loads(
            (root / "runtime" / LLAMA_CPP_BUILD / ".verified.json").read_text(
                encoding="utf-8"
            )
        )
        if (
            marker.get("archive_sha256") == artifact.sha256
            and marker.get("server_sha256") == _sha256(installed)
        ):
            return installed
    except (OSError, RuntimeError, ValueError, AttributeError):
        pass
    downloads = root / "downloads"
    archive = downloads / artifact.filename
    _download_verified(artifact.url, archive, artifact.sha256, artifact.size, "AI runtime")
    staging = root / f"runtime-{LLAMA_CPP_BUILD}.installing"
    destination = root / "runtime" / LLAMA_CPP_BUILD
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    _safe_extract(archive, staging)
    server_name = "llama-server.exe" if os.name == "nt" else "llama-server"
    matches = list(staging.rglob(server_name))
    if not matches:
        raise RuntimeError("Verified AI runtime archive does not contain llama-server.")
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(destination)
    executable = next(destination.rglob(server_name))
    executable.chmod(executable.stat().st_mode | 0o111)
    (destination / ".verified.json").write_text(
        json.dumps(
            {
                "llama_cpp_build": LLAMA_CPP_BUILD,
                "archive_sha256": artifact.sha256,
                "server_sha256": _sha256(executable),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return executable


def _ensure_model(root: Path) -> Path:
    model = root / "models" / BUILTIN_MODEL_FILE
    _download_verified(
        BUILTIN_MODEL_URL, model, BUILTIN_MODEL_SHA256, BUILTIN_MODEL_SIZE, "AI model"
    )
    return model


def _server_executable(root: Path) -> Path:
    name = "llama-server.exe" if os.name == "nt" else "llama-server"
    candidates = list((root / "runtime" / LLAMA_CPP_BUILD).rglob(name))
    if not candidates:
        raise RuntimeError("Built-in AI runtime is not installed.")
    return candidates[0]


def _download_verified(
    url: str, destination: Path, expected_sha256: str, expected_size: int, label: str
) -> None:
    if destination.is_file() and destination.stat().st_size == expected_size:
        if _sha256(destination) == expected_sha256:
            return
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    required = expected_size + DOWNLOAD_HEADROOM_BYTES
    if shutil.disk_usage(destination.parent).free < required:
        raise RuntimeError(
            f"Not enough free space for {label}; at least "
            f"{required / (1024**3):.1f} GB is required."
        )
    temporary = destination.with_suffix(destination.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    print(f"[AI-INSTALL] Downloading {label} | bytes={expected_size}")
    request = Request(url, headers={"User-Agent": "YouTube-Media-Studio/3"})
    try:
        with urlopen(request, timeout=BUILTIN_DOWNLOAD_TIMEOUT_SECONDS) as response:
            _copy_response(response, temporary, expected_size, label)
    except (OSError, URLError) as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Could not download the built-in {label}: {exc}") from exc
    actual_size = temporary.stat().st_size
    if actual_size != expected_size or _sha256(temporary) != expected_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"The downloaded {label} failed size or SHA-256 verification.")
    temporary.replace(destination)
    print(f"[AI-INSTALL] Verified {label} | path={destination}")


def _copy_response(response: BinaryIO, target: Path, total: int, label: str) -> None:
    copied = 0
    next_report = 10
    with target.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            copied += len(chunk)
            percent = int(copied * 100 / max(1, total))
            if percent >= next_report:
                print(f"[AI-INSTALL] {label} {min(percent, 100)}%")
                next_report += 10


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    if archive.suffix.casefold() == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                _validate_archive_member(destination, member.filename)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise RuntimeError("AI runtime archive contains an unsupported link.")
            bundle.extractall(destination)
        return
    with tarfile.open(archive, mode="r:gz") as bundle:
        for member in bundle.getmembers():
            _validate_archive_member(destination, member.name)
            if member.issym() or member.islnk():
                raise RuntimeError("AI runtime archive contains an unsupported link.")
        bundle.extractall(destination)


def _validate_archive_member(destination: Path, member: str) -> None:
    target = (destination / member).resolve()
    if not target.is_relative_to(destination):
        raise RuntimeError("AI runtime archive contains an unsafe path.")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _server_ready(base_url: str) -> bool:
    if not base_url:
        return False
    try:
        with urlopen(f"{base_url.removesuffix('/v1')}/health", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("status") == "ok"
    except (OSError, URLError, ValueError):
        return False


def _cpu_threads() -> int:
    available = os.cpu_count() or 2
    # Keep a core (and on larger systems several threads) free for playback and UI.
    return max(1, min(8, available - max(1, available // 4)))


atexit.register(stop_builtin_server)
