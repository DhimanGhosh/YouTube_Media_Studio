"""Shared NVIDIA NIM, Ollama, and deterministic-fallback AI gateway."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NVIDIA_API_KEY_ENV = "NVIDIA_API_KEY"
NVIDIA_MODEL_ENV = "YOUTUBE_MEDIA_STUDIO_NVIDIA_MODEL"
OLLAMA_MODEL_ENV = "YOUTUBE_MEDIA_STUDIO_OLLAMA_MODEL"
DEFAULT_NVIDIA_MODEL = "z-ai/glm-5.2"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
NVIDIA_ATTEMPT_TIMEOUT_SECONDS = 6.0
OLLAMA_ATTEMPT_TIMEOUT_SECONDS = 20.0
NVIDIA_FAILURE_COOLDOWN_SECONDS = 60.0

_PROVIDER_STATE_LOCK = threading.Lock()
_nvidia_probe_in_flight = False
_nvidia_unavailable_until = 0.0


class AIUnavailableError(RuntimeError):
    """Raised after every configured AI provider has failed."""


@dataclass(frozen=True, slots=True)
class AIResponse:
    data: dict[str, Any]
    provider: str
    model: str


def configure_ai_environment(
    *, nvidia_api_key: str = "", nvidia_model: str = "", ollama_model: str = ""
) -> None:
    """Configure process-wide AI credentials without passing secrets through jobs/logs."""

    key = nvidia_api_key.strip()
    if key:
        os.environ[NVIDIA_API_KEY_ENV] = key
    else:
        os.environ.pop(NVIDIA_API_KEY_ENV, None)
    for name, value in (
        (NVIDIA_MODEL_ENV, nvidia_model.strip()),
        (OLLAMA_MODEL_ENV, ollama_model.strip()),
    ):
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    _reset_provider_state()


def _reset_provider_state() -> None:
    """Allow a fresh NVIDIA probe after credentials or models change."""

    global _nvidia_probe_in_flight, _nvidia_unavailable_until
    with _PROVIDER_STATE_LOCK:
        _nvidia_probe_in_flight = False
        _nvidia_unavailable_until = 0.0


def _begin_nvidia_probe() -> tuple[bool, str]:
    """Permit one bounded probe and make every concurrent caller fall through."""

    global _nvidia_probe_in_flight
    now = time.monotonic()
    with _PROVIDER_STATE_LOCK:
        if _nvidia_unavailable_until > now:
            remaining = max(1, round(_nvidia_unavailable_until - now))
            return False, f"recent failure; retry available in {remaining}s"
        if _nvidia_probe_in_flight:
            return False, "another availability probe is already running"
        _nvidia_probe_in_flight = True
        return True, ""


def _finish_nvidia_probe(*, available: bool) -> None:
    global _nvidia_probe_in_flight, _nvidia_unavailable_until
    with _PROVIDER_STATE_LOCK:
        _nvidia_probe_in_flight = False
        _nvidia_unavailable_until = (
            0.0
            if available
            else time.monotonic() + NVIDIA_FAILURE_COOLDOWN_SECONDS
        )


def configured_primary_model(requested_model: str = "") -> str:
    """Return the effective NVIDIA model, otherwise the Ollama fallback model."""

    if os.environ.get(NVIDIA_API_KEY_ENV, "").strip():
        return os.environ.get(NVIDIA_MODEL_ENV, "").strip() or requested_model.strip()
    return os.environ.get(OLLAMA_MODEL_ENV, "").strip() or requested_model.strip()


def chat_json(
    messages: Sequence[Mapping[str, str]],
    schema: Mapping[str, Any],
    *,
    model: str = "",
    timeout: float = 90.0,
    temperature: float = 0.0,
    max_tokens: int = 4096,
) -> AIResponse:
    """Use NVIDIA first, then Ollama, and report failure for deterministic fallback."""

    errors: list[str] = []
    api_key = os.environ.get(NVIDIA_API_KEY_ENV, "").strip()
    if api_key:
        nvidia_model = (
            os.environ.get(NVIDIA_MODEL_ENV, "").strip()
            or model.strip()
            or DEFAULT_NVIDIA_MODEL
        )
        probe, bypass_reason = _begin_nvidia_probe()
        if probe:
            try:
                data = _call_nvidia(
                    messages,
                    schema,
                    api_key=api_key,
                    model=nvidia_model,
                    timeout=min(max(0.1, timeout), NVIDIA_ATTEMPT_TIMEOUT_SECONDS),
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                _finish_nvidia_probe(available=True)
                print(f"[AI-PROVIDER] NVIDIA NIM | model={nvidia_model}")
                return AIResponse(data, "NVIDIA NIM", nvidia_model)
            except Exception as exc:
                _finish_nvidia_probe(available=False)
                safe_error = _safe_error(exc).replace(api_key, "[redacted]")
                errors.append(f"NVIDIA NIM: {safe_error}")
                print(f"[AI-PROVIDER-FALLBACK] NVIDIA NIM unavailable | {safe_error}")
        else:
            errors.append(f"NVIDIA NIM: bypassed ({bypass_reason})")
            print(f"[AI-PROVIDER-FALLBACK] NVIDIA NIM bypassed | {bypass_reason}")

    ollama_model = os.environ.get(OLLAMA_MODEL_ENV, "").strip()
    if not ollama_model and not api_key:
        ollama_model = model.strip()
    if not ollama_model:
        errors.append("Ollama: no model configured")
        print(
            "[AI-STATIC-FALLBACK] No Ollama model configured | "
            "continuing with deterministic internet evidence"
        )
        raise AIUnavailableError("; ".join(errors))
    try:
        data = _call_ollama(
            messages,
            schema,
            model=ollama_model,
            timeout=min(max(0.1, timeout), OLLAMA_ATTEMPT_TIMEOUT_SECONDS),
            temperature=temperature,
            max_tokens=max_tokens,
        )
        print(f"[AI-PROVIDER] Ollama | model={ollama_model}")
        return AIResponse(data, "Ollama", ollama_model)
    except Exception as exc:
        safe_error = _safe_error(exc)
        errors.append(f"Ollama: {safe_error}")
        print(f"[AI-PROVIDER-FALLBACK] Ollama unavailable | {safe_error}")

    print(
        "[AI-STATIC-FALLBACK] AI providers unavailable | "
        "continuing with deterministic internet evidence"
    )
    raise AIUnavailableError("; ".join(errors) or "No AI provider is configured")


def _call_nvidia(
    messages: Sequence[Mapping[str, str]],
    schema: Mapping[str, Any],
    *,
    api_key: str,
    model: str,
    timeout: float,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    schema_instruction = (
        "Return only one valid JSON object matching this JSON Schema. Do not use Markdown: "
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )
    prepared = [dict(message) for message in messages]
    if prepared and prepared[0].get("role") == "system":
        prepared[0]["content"] = f"{prepared[0].get('content', '')}\n\n{schema_instruction}"
    else:
        prepared.insert(0, {"role": "system", "content": schema_instruction})
    body = {
        "model": model,
        "messages": prepared,
        "temperature": max(0.0, min(float(temperature), 1.0)),
        "top_p": 1,
        "max_tokens": max(1, min(int(max_tokens), 16384)),
        "seed": 42,
        "stream": False,
    }
    request = Request(
        NVIDIA_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed NVIDIA URL
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    choices = payload.get("choices", []) if isinstance(payload, dict) else []
    message = choices[0].get("message", {}) if choices and isinstance(choices[0], dict) else {}
    content = str(message.get("content") or "") if isinstance(message, dict) else ""
    return _parse_json_object(content)


def _call_ollama(
    messages: Sequence[Mapping[str, str]],
    schema: Mapping[str, Any],
    *,
    model: str,
    timeout: float,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": list(messages),
        "stream": False,
        "format": dict(schema),
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
        },
        "keep_alive": "5m",
    }
    request = Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - local Ollama
        payload = json.loads(response.read().decode("utf-8"))
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    content = str(message.get("content") or "") if isinstance(message, dict) else ""
    return _parse_json_object(content)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise ValueError("Provider returned no content")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("Provider returned non-JSON content") from None
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("Provider response is not a JSON object")
    return parsed


def _safe_error(exc: Exception) -> str:
    """Return a bounded error message that can never contain an API key."""

    return " ".join(str(exc).split())[:400] or type(exc).__name__
