from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

import pytest

from youtube_audio_video_downloader.services.ai import ai_provider


class Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


MESSAGES = [{"role": "user", "content": "classify"}]
SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.fixture(autouse=True)
def reset_provider_circuit() -> None:
    ai_provider._reset_provider_state()
    yield
    ai_provider._reset_provider_state()


def configure(monkeypatch, *, key: str = "") -> None:
    monkeypatch.setenv(ai_provider.AI_PROVIDER_ENV, "nvidia" if key else "ollama")
    monkeypatch.setenv(ai_provider.NVIDIA_API_KEY_ENV, key)
    monkeypatch.setenv(ai_provider.NVIDIA_MODEL_ENV, "z-ai/glm-5.2")
    monkeypatch.setenv(ai_provider.OLLAMA_MODEL_ENV, "qwen:test")


def test_configured_primary_identity_reports_selected_hosted_provider(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv(ai_provider.AI_PROVIDER_ENV, "groq")
    monkeypatch.setenv(ai_provider.AI_PROVIDER_API_KEY_ENV, "groq-secret")
    monkeypatch.setenv(ai_provider.AI_PROVIDER_MODEL_ENV, "openai/gpt-oss-120b")

    assert ai_provider.configured_primary_identity() == (
        "Groq",
        "openai/gpt-oss-120b",
    )


def test_configured_primary_identity_reports_real_ollama_fallback(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv(ai_provider.AI_PROVIDER_ENV, "groq")
    monkeypatch.delenv(ai_provider.AI_PROVIDER_API_KEY_ENV, raising=False)
    monkeypatch.setenv(ai_provider.AI_PROVIDER_MODEL_ENV, "hosted-model")

    assert ai_provider.configured_primary_identity() == ("Ollama", "qwen:test")


def test_configured_primary_identity_reports_nvidia(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv(ai_provider.AI_PROVIDER_ENV, "nvidia")
    monkeypatch.setenv(ai_provider.NVIDIA_API_KEY_ENV, "nvidia-secret")
    monkeypatch.setenv(ai_provider.NVIDIA_MODEL_ENV, "nvidia-model")

    assert ai_provider.configured_primary_identity() == (
        "NVIDIA NIM",
        "nvidia-model",
    )


def test_configured_primary_identity_reports_keyless_custom_provider(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv(ai_provider.AI_PROVIDER_ENV, "custom")
    monkeypatch.delenv(ai_provider.AI_PROVIDER_API_KEY_ENV, raising=False)
    monkeypatch.setenv(ai_provider.AI_PROVIDER_MODEL_ENV, "custom-model")

    assert ai_provider.configured_primary_identity() == (
        "Custom OpenAI-compatible",
        "custom-model",
    )


def test_nvidia_is_used_first_when_key_is_configured(monkeypatch) -> None:
    configure(monkeypatch, key="nvapi-secret")
    response = Response({"choices": [{"message": {"content": '{"answer":"nvidia"}'}}]})
    with patch.object(ai_provider, "urlopen", return_value=response) as open_mock:
        result = ai_provider.chat_json(MESSAGES, SCHEMA)

    request = open_mock.call_args.args[0]
    assert request.full_url == ai_provider.NVIDIA_API_URL
    assert request.headers["Authorization"] == "Bearer nvapi-secret"
    assert result.data == {"answer": "nvidia"}
    assert result.provider == "NVIDIA NIM"
    assert result.model == "z-ai/glm-5.2"


def test_nvidia_failure_falls_back_to_ollama(monkeypatch) -> None:
    configure(monkeypatch, key="nvapi-secret")
    ollama = Response({"message": {"content": '{"answer":"ollama"}'}})
    with patch.object(ai_provider, "urlopen", side_effect=[URLError("offline"), ollama]):
        result = ai_provider.chat_json(MESSAGES, SCHEMA)

    assert result.data == {"answer": "ollama"}
    assert result.provider == "Ollama"
    assert result.model == "qwen:test"


def test_recent_nvidia_failure_is_bypassed_and_timeouts_are_bounded(monkeypatch) -> None:
    configure(monkeypatch, key="nvapi-secret")
    calls: list[tuple[str, float]] = []

    def provider(request, *, timeout):
        calls.append((request.full_url, timeout))
        if request.full_url == ai_provider.NVIDIA_API_URL:
            raise URLError("offline")
        return Response({"message": {"content": '{"answer":"ollama"}'}})

    with patch.object(ai_provider, "urlopen", side_effect=provider):
        first = ai_provider.chat_json(MESSAGES, SCHEMA, timeout=90)
        second = ai_provider.chat_json(MESSAGES, SCHEMA, timeout=90)

    assert first.provider == second.provider == "Ollama"
    assert [url for url, _timeout in calls].count(ai_provider.NVIDIA_API_URL) == 1
    assert calls[0][1] == ai_provider.NVIDIA_ATTEMPT_TIMEOUT_SECONDS
    assert all(
        timeout <= ai_provider.OLLAMA_ATTEMPT_TIMEOUT_SECONDS
        for url, timeout in calls
        if url.endswith("/api/chat")
    )


def test_ollama_gets_full_default_budget_for_cold_model_start(monkeypatch) -> None:
    configure(monkeypatch)
    ollama = Response({"message": {"content": '{"answer":"local"}'}})

    with patch.object(ai_provider, "urlopen", return_value=ollama) as open_mock:
        ai_provider.chat_json(MESSAGES, SCHEMA)

    assert open_mock.call_args.kwargs["timeout"] == 90.0


def test_ollama_disables_thinking_for_structured_qwen_response(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv(ai_provider.OLLAMA_MODEL_ENV, "qwen3.5:9b")
    ollama = Response({"message": {"content": '{"answer":"local"}'}})

    with patch.object(ai_provider, "urlopen", return_value=ollama) as open_mock:
        result = ai_provider.chat_json(MESSAGES, SCHEMA)

    request = open_mock.call_args.args[0]
    body = json.loads(request.data.decode("utf-8"))
    assert body["think"] is False
    assert body["options"]["num_ctx"] == ai_provider.OLLAMA_CONTEXT_WINDOW
    assert result.data == {"answer": "local"}


def test_no_key_uses_ollama_directly(monkeypatch) -> None:
    configure(monkeypatch)
    ollama = Response({"message": {"content": '{"answer":"local"}'}})
    with patch.object(ai_provider, "urlopen", return_value=ollama) as open_mock:
        result = ai_provider.chat_json(MESSAGES, SCHEMA)

    assert open_mock.call_count == 1
    assert open_mock.call_args.args[0].full_url.endswith("/api/chat")
    assert result.provider == "Ollama"


def test_builtin_cpu_provider_works_without_ollama_or_api_key(monkeypatch) -> None:
    monkeypatch.setenv(ai_provider.AI_PROVIDER_ENV, "builtin")
    monkeypatch.delenv(ai_provider.OLLAMA_MODEL_ENV, raising=False)
    response = Response(
        {"choices": [{"message": {"content": '{"answer":"private"}'}}]}
    )
    with (
        patch.object(
            ai_provider,
            "ensure_builtin_server",
            return_value=("http://127.0.0.1:12345/v1", "Qwen3-0.6B-Q8_0"),
        ),
        patch.object(ai_provider, "urlopen", return_value=response) as open_mock,
    ):
        result = ai_provider.chat_json(MESSAGES, SCHEMA)

    assert open_mock.call_args.args[0].full_url.endswith("/v1/chat/completions")
    assert result.provider == "Built-in CPU AI"
    assert result.model == "Qwen3-0.6B-Q8_0"
    assert result.data == {"answer": "private"}


def test_selected_generic_provider_routes_legacy_structured_calls_through_agno(
    monkeypatch,
) -> None:
    monkeypatch.setenv("YOUTUBE_MEDIA_STUDIO_AI_PROVIDER", "openai")
    with patch(
        "youtube_audio_video_downloader.services.ai.agno_provider.run_structured_json_agent",
        return_value=({"answer": "hosted"}, "OpenAI", "gpt-test"),
    ) as run_agent:
        result = ai_provider.chat_json(
            [{"role": "user", "content": "question"}],
            SCHEMA,
            model="gpt-test",
        )
    assert result.data == {"answer": "hosted"}
    assert result.provider == "OpenAI"
    assert run_agent.call_args.kwargs["output_schema"] == SCHEMA


def test_all_provider_failures_raise_for_static_caller_fallback(monkeypatch) -> None:
    configure(monkeypatch, key="nvapi-do-not-log")
    with (
        patch.object(ai_provider, "urlopen", side_effect=URLError("offline")),
        pytest.raises(ai_provider.AIUnavailableError) as error,
    ):
        ai_provider.chat_json(MESSAGES, SCHEMA)

    assert "NVIDIA NIM" in str(error.value)
    assert "Ollama" in str(error.value)
    assert "nvapi-do-not-log" not in str(error.value)


def test_clearing_external_models_tries_built_in_before_static_fallback(monkeypatch) -> None:
    monkeypatch.delenv(ai_provider.NVIDIA_API_KEY_ENV, raising=False)
    monkeypatch.delenv(ai_provider.NVIDIA_MODEL_ENV, raising=False)
    monkeypatch.delenv(ai_provider.OLLAMA_MODEL_ENV, raising=False)
    ai_provider.configure_ai_environment(
        nvidia_api_key="",
        nvidia_model="",
        ollama_model="",
    )

    with (
        patch.object(ai_provider, "urlopen") as open_mock,
        patch.object(
            ai_provider,
            "ensure_builtin_server",
            side_effect=RuntimeError("built-in unavailable"),
        ) as ensure_builtin,
        pytest.raises(ai_provider.AIUnavailableError, match="built-in unavailable"),
    ):
        ai_provider.chat_json(MESSAGES, SCHEMA)

    open_mock.assert_not_called()
    ensure_builtin.assert_called_once_with()
