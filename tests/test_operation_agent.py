"""Tests for the evidence-bounded operation AI preflight."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from youtube_audio_video_downloader.core.cancellation import CancellationToken
from youtube_audio_video_downloader.gui.operations import execute_operation
from youtube_audio_video_downloader.services.ai_provider import AIResponse
from youtube_audio_video_downloader.services.operation_agent import (
    OPERATION_PREFLIGHT_TIMEOUT_SECONDS,
    OperationPreflightDecision,
    preflight_operation,
)


class OperationAgentTest(unittest.TestCase):
    def test_uses_global_model_and_preserves_explicit_references_verbatim(self) -> None:
        captured: list[dict[str, object]] = []

        def answer(messages, schema, **kwargs) -> AIResponse:
            captured.append({"messages": messages, "schema": schema, "kwargs": kwargs})
            return AIResponse(
                {"action": "proceed", "reason": "Request is coherent", "concerns": []},
                "Ollama", "global:test",
            )

        path = r"D:\Media\Album (2006)"
        url = "https://youtu.be/aBc_123?t=42"
        timestamp = "00:03:17.250"
        with patch(
            "youtube_audio_video_downloader.services.operation_agent.chat_json",
            side_effect=answer,
        ):
            decision = preflight_operation(
                "album",
                {
                    "agentic_model": "global:test",
                    "output_dir": path,
                    "input_data": {"Track": {"ytb_link": url, "timestamp": timestamp}},
                },
            )

        self.assertEqual(decision.action, "proceed")
        self.assertEqual(captured[0]["kwargs"]["model"], "global:test")  # type: ignore[index]
        self.assertEqual(
            captured[0]["kwargs"]["timeout"],  # type: ignore[index]
            OPERATION_PREFLIGHT_TIMEOUT_SECONDS,
        )
        user_payload = json.loads(captured[0]["messages"][1]["content"])  # type: ignore[index]
        references = user_payload["evidence"]["immutable_references"]
        self.assertIn({"field": "params.output_dir", "value": path}, references)
        self.assertIn({"field": "params.input_data.Track.ytb_link", "value": url}, references)
        self.assertIn(
            {"field": "params.input_data.Track.timestamp", "value": timestamp}, references
        )
        self.assertEqual(
            set(captured[0]["schema"]["properties"]),  # type: ignore[index]
            {"action", "reason", "concerns"},
        )
        self.assertEqual(len(captured), 2)

    def test_outage_fails_open_without_changing_user_directed_operation(self) -> None:
        with patch(
            "youtube_audio_video_downloader.services.operation_agent.chat_json",
            side_effect=TimeoutError("offline"),
        ):
            decision = preflight_operation(
                "audio_trimmer",
                {"agentic_model": "global:test", "input_path": r"D:\song.mp3"},
            )
        self.assertEqual(decision.action, "proceed")
        self.assertTrue(decision.fallback)
        self.assertIn("preserving the explicit request", decision.reason)

    def test_strict_schema_rejects_additional_output_fields(self) -> None:
        result = AIResponse(
            {
                "action": "review", "reason": "Change the path", "concerns": ["path"],
                "replacement_path": r"D:\other",
            },
            "Ollama", "global:test",
        )
        with patch(
            "youtube_audio_video_downloader.services.operation_agent.chat_json",
            return_value=result,
        ):
            decision = preflight_operation("edit_media", {"agentic_model": "global:test"})
        self.assertEqual(decision.action, "proceed")
        self.assertTrue(decision.fallback)

    def test_execute_operation_runs_preflight_for_deterministic_workflow(self) -> None:
        decision = OperationPreflightDecision("proceed", "audited", model="global:test")
        with patch(
            "youtube_audio_video_downloader.gui.operations.preflight_operation",
            return_value=decision,
        ) as preflight:
            summary = execute_operation(
                "format_artists",
                {"input_text": "Sonu Nigam & Shreya Ghoshal", "agentic_model": "global:test"},
                CancellationToken(),
            )
        preflight.assert_called_once()
        self.assertEqual(preflight.call_args.args[0], "format_artists")
        self.assertEqual(summary.output_text, "Sonu Nigam, Shreya Ghoshal")


if __name__ == "__main__":
    unittest.main()
