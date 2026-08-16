from __future__ import annotations

from youtube_audio_video_downloader import (
    SUPPORTED_OPERATIONS,
    CancellationToken,
    MediaStudio,
    Operation,
    OperationSummary,
    run_operation,
)


def test_public_api_covers_every_gui_operation() -> None:
    assert tuple(item.value for item in Operation) == SUPPORTED_OPERATIONS
    client = MediaStudio()
    assert client.operations == SUPPORTED_OPERATIONS
    assert all(callable(getattr(client, name)) for name in SUPPORTED_OPERATIONS)


def test_public_api_runs_gui_executor_without_qt(monkeypatch) -> None:
    seen = {}

    def fake_execute(operation, params, token):
        seen.update(operation=operation, params=params, token=token)
        return OperationSummary(operation=operation, total=1)

    monkeypatch.setattr("youtube_audio_video_downloader.api.execute_operation", fake_execute)
    token = CancellationToken()
    summary = run_operation(
        Operation.FORMAT_ARTISTS,
        {"input_text": "one"},
        cancellation_token=token,
        ai_enabled=False,
    )

    assert summary == OperationSummary(operation="format_artists", total=1)
    assert seen == {
        "operation": "format_artists",
        "params": {"input_text": "one", "ai_enabled": False},
        "token": token,
    }


def test_media_studio_merges_defaults_params_and_keyword_overrides(monkeypatch) -> None:
    seen = {}

    def fake_run(operation, params, *, cancellation_token):
        seen.update(operation=operation, params=params, token=cancellation_token)
        return OperationSummary(operation=str(operation))

    monkeypatch.setattr("youtube_audio_video_downloader.api.run_operation", fake_run)
    client = MediaStudio(defaults={"workers": 4, "ai_enabled": True})
    summary = client.audio({"workers": 2, "output_dir": "music"}, ai_enabled=False)

    assert summary.operation == "audio"
    assert seen["operation"] == Operation.AUDIO
    assert seen["params"] == {"workers": 2, "ai_enabled": False, "output_dir": "music"}
    assert seen["token"] is client.cancellation_token


def test_public_api_rejects_unknown_operation_before_execution() -> None:
    try:
        run_operation("not-real", ai_enabled=False)
    except ValueError as exc:
        assert "Choose one of" in str(exc)
    else:
        raise AssertionError("unknown operation should fail")
