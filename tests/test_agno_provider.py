"""Provider selection tests for the Agno model factory."""

from __future__ import annotations

import pytest
from agno.models.anthropic import Claude
from agno.models.ollama import Ollama
from agno.models.openai import OpenAIChat

from youtube_audio_video_downloader.services.agno_provider import (
    _configured_models,
    configure_agno_environment,
)


@pytest.fixture(autouse=True)
def clean_provider_environment(monkeypatch):
    for name in (
        "YOUTUBE_MEDIA_STUDIO_AI_PROVIDER",
        "YOUTUBE_MEDIA_STUDIO_AI_API_KEY",
        "YOUTUBE_MEDIA_STUDIO_AI_MODEL",
        "YOUTUBE_MEDIA_STUDIO_AI_BASE_URL",
        "NVIDIA_API_KEY",
        "YOUTUBE_MEDIA_STUDIO_NVIDIA_MODEL",
        "YOUTUBE_MEDIA_STUDIO_OLLAMA_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def models() -> tuple[object, list[object]]:
    return _configured_models(
        requested_model="fallback:model",
        timeout=30,
        temperature=0.1,
        max_tokens=500,
    )


def test_ollama_is_a_keyless_primary_provider(monkeypatch) -> None:
    configure_agno_environment(provider="ollama", model="")
    monkeypatch.setenv("YOUTUBE_MEDIA_STUDIO_OLLAMA_MODEL", "qwen:test")
    primary, fallbacks = models()
    assert isinstance(primary, Ollama)
    assert primary.id == "qwen:test"
    assert primary.options["num_ctx"] == 16_384
    assert fallbacks == []


@pytest.mark.parametrize(
    ("provider", "expected_type", "expected_url"),
    [
        ("openai", OpenAIChat, None),
        ("google", OpenAIChat, "generativelanguage.googleapis.com"),
        ("groq", OpenAIChat, "api.groq.com"),
        ("huggingface", OpenAIChat, "router.huggingface.co"),
        ("openrouter", OpenAIChat, "openrouter.ai"),
        ("opencode", OpenAIChat, "opencode.ai"),
        ("anthropic", Claude, None),
    ],
)
def test_hosted_provider_uses_its_agno_adapter_and_ollama_fallback(
    monkeypatch, provider, expected_type, expected_url
) -> None:
    configure_agno_environment(provider=provider, api_key="secret", model="test-model")
    monkeypatch.setenv("YOUTUBE_MEDIA_STUDIO_OLLAMA_MODEL", "qwen:fallback")
    primary, fallbacks = models()
    assert isinstance(primary, expected_type)
    assert primary.id == "test-model"
    assert len(fallbacks) == 1
    assert isinstance(fallbacks[0], Ollama)
    if expected_url:
        assert expected_url in str(primary.base_url)


def test_custom_provider_requires_model_but_can_use_a_keyless_local_endpoint(
    monkeypatch,
) -> None:
    configure_agno_environment(
        provider="custom",
        model="local-model",
        base_url="http://127.0.0.1:1234/v1",
    )
    monkeypatch.setenv("YOUTUBE_MEDIA_STUDIO_OLLAMA_MODEL", "qwen:fallback")
    primary, _fallbacks = models()
    assert isinstance(primary, OpenAIChat)
    assert str(primary.base_url).rstrip("/") == "http://127.0.0.1:1234/v1"


def test_hosted_provider_without_key_uses_ollama(monkeypatch) -> None:
    configure_agno_environment(provider="openai", model="gpt-test")
    monkeypatch.setenv("YOUTUBE_MEDIA_STUDIO_OLLAMA_MODEL", "qwen:fallback")
    primary, fallbacks = models()
    assert isinstance(primary, Ollama)
    assert primary.id == "qwen:fallback"
    assert fallbacks == []
