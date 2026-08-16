"""Stable provider metadata shared by Global Settings and Agno model creation."""

from __future__ import annotations

from dataclasses import dataclass


AI_PROVIDER_ENV = "YOUTUBE_MEDIA_STUDIO_AI_PROVIDER"
AI_PROVIDER_API_KEY_ENV = "YOUTUBE_MEDIA_STUDIO_AI_API_KEY"
AI_PROVIDER_MODEL_ENV = "YOUTUBE_MEDIA_STUDIO_AI_MODEL"
AI_PROVIDER_BASE_URL_ENV = "YOUTUBE_MEDIA_STUDIO_AI_BASE_URL"


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    id: str
    label: str
    default_model: str
    base_url: str = ""
    key_placeholder: str = "API key"
    protocol: str = "openai"


PROVIDERS: tuple[ProviderDefinition, ...] = (
    ProviderDefinition("ollama", "Ollama (local)", ""),
    ProviderDefinition(
        "nvidia", "NVIDIA NIM", "z-ai/glm-5.2",
        "https://integrate.api.nvidia.com/v1", "nvapi-…",
    ),
    ProviderDefinition("openai", "OpenAI", "gpt-5.4-mini", key_placeholder="sk-…"),
    ProviderDefinition(
        "anthropic", "Anthropic", "claude-sonnet-4-6", key_placeholder="sk-ant-…",
        protocol="anthropic",
    ),
    ProviderDefinition(
        "google", "Google Gemini", "gemini-2.5-flash",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    ProviderDefinition(
        "groq", "Groq", "openai/gpt-oss-120b",
        "https://api.groq.com/openai/v1",
    ),
    ProviderDefinition(
        "huggingface", "Hugging Face Inference", "openai/gpt-oss-120b:fastest",
        "https://router.huggingface.co/v1", "hf_…",
    ),
    ProviderDefinition(
        "openrouter", "OpenRouter", "openai/gpt-5.4-mini",
        "https://openrouter.ai/api/v1", "sk-or-…",
    ),
    ProviderDefinition(
        "opencode", "OpenCode Zen", "glm-5.2",
        "https://opencode.ai/zen/v1",
    ),
    ProviderDefinition(
        "custom", "Custom OpenAI-compatible", "", key_placeholder="Optional API key",
    ),
)

_BY_ID = {provider.id: provider for provider in PROVIDERS}


def provider_definition(provider_id: str) -> ProviderDefinition:
    return _BY_ID.get(str(provider_id).strip().casefold(), _BY_ID["ollama"])


def provider_ids() -> set[str]:
    return set(_BY_ID)
