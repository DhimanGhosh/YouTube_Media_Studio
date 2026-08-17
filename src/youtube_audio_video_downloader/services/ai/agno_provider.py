"""Agno agent factory backed by the application's configured provider chain."""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from agno.agent import Agent
from agno.models.base import Model
from agno.models.anthropic import Claude
from agno.models.nvidia import Nvidia
from agno.models.ollama import Ollama
from agno.models.openai import OpenAIChat
from pydantic import BaseModel

from youtube_audio_video_downloader.services.ai.ai_provider import (
    NVIDIA_API_KEY_ENV,
    NVIDIA_ATTEMPT_TIMEOUT_SECONDS,
    NVIDIA_MODEL_ENV,
    OLLAMA_CONTEXT_WINDOW,
    OLLAMA_MODEL_ENV,
)
from youtube_audio_video_downloader.services.ai.ai_provider_registry import (
    AI_PROVIDER_API_KEY_ENV,
    AI_PROVIDER_BASE_URL_ENV,
    AI_PROVIDER_ENV,
    AI_PROVIDER_MODEL_ENV,
    provider_definition,
    provider_ids,
)
from youtube_audio_video_downloader.services.ai.builtin_runtime import (
    BUILTIN_MODEL_ID,
    BUILTIN_PROVIDER_LABEL,
    ensure_builtin_server,
)


OutputT = TypeVar("OutputT", bound=BaseModel)


def configure_agno_environment(
    *, provider: str,
    api_key: str = "",
    model: str = "",
    base_url: str = "",
) -> None:
    """Apply the selected hosted/local provider without exposing its key to jobs."""

    selected = provider.strip().casefold()
    os.environ[AI_PROVIDER_ENV] = selected if selected in provider_ids() else "builtin"
    for name, value in (
        (AI_PROVIDER_API_KEY_ENV, api_key.strip()),
        (AI_PROVIDER_MODEL_ENV, model.strip()),
        (AI_PROVIDER_BASE_URL_ENV, base_url.strip()),
    ):
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)


def run_structured_agent(
    *,
    name: str,
    role: str,
    instructions: str,
    input_data: dict[str, Any],
    output_schema: type[OutputT],
    requested_model: str,
    timeout: float,
    temperature: float = 0,
    max_tokens: int = 2048,
) -> OutputT:
    """Run one real Agno agent with selected-provider-to-Ollama fallback."""

    primary, fallbacks = _configured_models(
        requested_model=requested_model,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    agent_options = {
        "name": name,
        "role": role,
        "instructions": instructions,
        "output_schema": output_schema,
        "parse_response": True,
        "telemetry": False,
        "stream": False,
    }
    result = _run_with_fallback(
        primary=primary,
        fallbacks=fallbacks,
        agent_options=agent_options,
        input_text=json.dumps(input_data, ensure_ascii=False),
    )
    content = result.content
    if isinstance(content, output_schema):
        parsed = content
    elif isinstance(content, dict):
        parsed = output_schema.model_validate(content)
    elif isinstance(content, str):
        parsed = output_schema.model_validate_json(content)
    else:
        parsed = output_schema.model_validate(content)
    print(
        f"[AI-AGENT-PROVIDER] {name} | "
        f"provider={result.model_provider or primary.provider} "
        f"model={result.model or primary.id}"
    )
    return parsed


def run_structured_json_agent(
    *,
    name: str,
    instructions: str,
    input_text: str,
    output_schema: dict[str, Any],
    requested_model: str,
    timeout: float,
    temperature: float,
    max_tokens: int,
) -> tuple[dict[str, Any], str, str]:
    """Run legacy schema-based AI calls through the selected Agno provider."""

    primary, fallbacks = _configured_models(
        requested_model=requested_model,
        timeout=timeout,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    agent_options = {
        "name": name,
        "instructions": instructions,
        "output_schema": output_schema,
        "parse_response": True,
        "telemetry": False,
        "stream": False,
    }
    result = _run_with_fallback(
        primary=primary,
        fallbacks=fallbacks,
        agent_options=agent_options,
        input_text=input_text,
    )
    content = result.content
    if isinstance(content, BaseModel):
        data = content.model_dump()
    elif isinstance(content, dict):
        data = content
    elif isinstance(content, str):
        data = json.loads(content)
    else:
        raise ValueError(f"{name} returned unsupported structured content")
    return data, str(result.model_provider or primary.provider), str(result.model or primary.id)


def _run_with_fallback(
    *,
    primary: Model,
    fallbacks: list[Model],
    agent_options: dict[str, Any],
    input_text: str,
):
    """Run configured models, lazily installing the built-in CPU fallback if needed."""

    agent = Agent(model=primary, fallback_models=fallbacks, **agent_options)
    try:
        return agent.run(input_text, stream=False)
    except Exception as primary_error:
        error: Exception = primary_error
        for fallback in fallbacks:
            print(
                "[AI-PROVIDER-FALLBACK] "
                f"{primary.provider} unavailable | {type(error).__name__} | "
                f"trying {fallback.provider}"
            )
            try:
                return Agent(model=fallback, **agent_options).run(input_text, stream=False)
            except Exception as fallback_error:
                error = fallback_error
        if str(primary.provider) == BUILTIN_PROVIDER_LABEL:
            raise error
        print(
            "[AI-PROVIDER-FALLBACK] "
            f"configured local providers unavailable | trying {BUILTIN_PROVIDER_LABEL}"
        )
        builtin = _builtin_model(
            timeout=90,
            temperature=0,
            max_tokens=4096,
        )
        return Agent(model=builtin, **agent_options).run(input_text, stream=False)


def _configured_models(
    *,
    requested_model: str,
    timeout: float,
    temperature: float,
    max_tokens: int,
) -> tuple[Model, list[Model]]:
    """Create Agno models while preserving the app's existing settings semantics."""

    configured_provider = os.environ.get(AI_PROVIDER_ENV, "").strip().casefold()
    if not configured_provider:
        configured_provider = (
            "nvidia" if os.environ.get(NVIDIA_API_KEY_ENV, "").strip() else "builtin"
        )
    definition = provider_definition(configured_provider)
    ollama_id = os.environ.get(OLLAMA_MODEL_ENV, "").strip()
    if not ollama_id and configured_provider == "ollama":
        requested = requested_model.strip()
        ollama_id = (
            requested if requested and requested != BUILTIN_MODEL_ID else ""
        )
    ollama = (
        Ollama(
            id=ollama_id,
            timeout=max(0.1, timeout),
            options={
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": OLLAMA_CONTEXT_WINDOW,
            },
            request_params={"think": False},
        )
        if ollama_id else None
    )
    if configured_provider == "builtin":
        return _builtin_model(
            timeout=timeout, temperature=temperature, max_tokens=max_tokens
        ), []
    if configured_provider == "ollama":
        if ollama is None:
            return _builtin_model(
                timeout=timeout, temperature=temperature, max_tokens=max_tokens
            ), []
        return ollama, []
    api_key = (
        os.environ.get(AI_PROVIDER_API_KEY_ENV, "").strip()
        or (
            os.environ.get(NVIDIA_API_KEY_ENV, "").strip()
            if configured_provider == "nvidia" else ""
        )
    )
    if not api_key and configured_provider != "custom":
        fallback_name = "Ollama" if ollama is not None else BUILTIN_PROVIDER_LABEL
        print(
            f"[AI-PROVIDER-FALLBACK] {definition.label} unavailable | "
            f"no API key configured | trying {fallback_name}"
        )
        if ollama is not None:
            return ollama, []
        return _builtin_model(
            timeout=timeout, temperature=temperature, max_tokens=max_tokens
        ), []
    model_id = (
        os.environ.get(AI_PROVIDER_MODEL_ENV, "").strip()
        or (
            os.environ.get(NVIDIA_MODEL_ENV, "").strip()
            if configured_provider == "nvidia" else ""
        )
        or requested_model.strip()
        or definition.default_model
    )
    if not model_id:
        raise ValueError(f"No model is configured for {definition.label}.")
    provider_timeout = min(max(0.1, timeout), NVIDIA_ATTEMPT_TIMEOUT_SECONDS)
    if configured_provider == "nvidia":
        primary: Model = Nvidia(
            id=model_id,
            api_key=api_key,
            timeout=provider_timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=0,
        )
    elif configured_provider == "anthropic":
        primary = Claude(
            id=model_id,
            api_key=api_key,
            timeout=max(0.1, timeout),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        base_url = (
            os.environ.get(AI_PROVIDER_BASE_URL_ENV, "").strip()
            or definition.base_url
            or None
        )
        primary = OpenAIChat(
            id=model_id,
            name=definition.label,
            provider=definition.label,
            api_key=api_key or "not-required",
            base_url=base_url,
            timeout=max(0.1, timeout),
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=0,
            supports_native_structured_outputs=configured_provider == "openai",
        )
    return primary, [ollama] if ollama is not None else []


def _builtin_model(*, timeout: float, temperature: float, max_tokens: int) -> OpenAIChat:
    base_url, model_id = ensure_builtin_server()
    return OpenAIChat(
        id=model_id,
        name=BUILTIN_PROVIDER_LABEL,
        provider=BUILTIN_PROVIDER_LABEL,
        api_key="local",
        base_url=base_url,
        timeout=max(0.1, timeout),
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=0,
        supports_native_structured_outputs=True,
    )
