"""Truthful, centralized disclosure of AI use by desktop operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AiUsage:
    """Describe whether an operation invokes a model and why."""

    active: bool
    model: str
    purpose: str

    @property
    def badge_text(self) -> str:
        if self.active:
            return f"AI ON · {self.model} · {self.purpose}"
        return f"AI NOT USED · {self.purpose}"

    @property
    def log_text(self) -> str:
        state = "AI-ENABLED" if self.active else "AI-NOT-USED"
        model = f" | model={self.model}" if self.model else ""
        return f"[{state}] {self.purpose}{model}"


def operation_ai_usage(operation: str, params: Mapping[str, object]) -> AiUsage:
    """Return the exact AI contract for one concrete operation invocation."""

    name = str(operation or "").strip()
    if not bool(params.get("ai_enabled", True)):
        return AiUsage(
            False,
            "",
            "Internet and deterministic rules only; AI disabled for this task",
        )
    selected_model = params.get("agentic_model") or params.get("model")
    if name == "search_song":
        return _usage(selected_model, "Preflight + search intent interpretation")
    if name == "enrich_song":
        return _usage(selected_model, "Preflight + selected-track identity verification")
    if name == "album_metadata_enricher":
        return _usage(selected_model, "Preflight + track metadata verification")
    if name == "album_consolidator":
        return _usage(selected_model, "Preflight + pre-move track verification")
    if name in {"audio", "video", "album", "jukebox"}:
        suffix = (
            "operation preflight only"
            if not bool(params.get("auto_enrich_downloads", True))
            or (name == "video" and bool(params.get("info_mode", False)))
            else "preflight + post-download metadata verification"
        )
        return _usage(selected_model, suffix.title())
    return _usage(selected_model, "Operation preflight and consistency audit")


def _usage(model: object, purpose: str) -> AiUsage:
    selected = str(model or "").strip()
    if not selected:
        return AiUsage(False, "", f"{purpose}; no model configured")
    return AiUsage(True, selected, purpose)
