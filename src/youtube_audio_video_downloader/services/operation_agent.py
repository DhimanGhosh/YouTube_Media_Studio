"""Evidence-bounded AI preflight for desktop operations.

The preflight can point out ambiguity before a workflow runs, but it cannot
change arguments or authorize a metadata mutation.  Paths, URLs, and timestamp
values are copied verbatim into immutable evidence and are never returned by
the model.  Existing metadata verification remains the only gate allowed to
change media identity fields.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from youtube_audio_video_downloader.services.ai_provider import chat_json


@dataclass(frozen=True, slots=True)
class OperationPreflightDecision:
    """A validated, advisory assessment of one requested operation."""

    action: str
    reason: str
    concerns: tuple[str, ...] = ()
    model: str = ""
    fallback: bool = False


_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["proceed", "review"]},
        "reason": {"type": "string"},
        "concerns": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["action", "reason", "concerns"],
    "additionalProperties": False,
}

_REFERENCE_MARKERS = (
    "path",
    "folder",
    "dir",
    "url",
    "link",
    "timestamp",
)
_TIMESTAMP_KEYS = {"start", "end", "start_time", "end_time", "from_time", "to_time"}
_TIMESTAMP_PATTERN = re.compile(r"^\s*\d{1,2}(?::\d{2}){1,2}(?:\.\d+)?\s*$")
_MODEL_KEYS = {"model", "agentic_model"}
OPERATION_PREFLIGHT_TIMEOUT_SECONDS = 90.0


def preflight_operation(
    operation: str,
    params: Mapping[str, object],
    *,
    timeout: float = OPERATION_PREFLIGHT_TIMEOUT_SECONDS,
) -> OperationPreflightDecision:
    """Audit an operation using NVIDIA first and local Ollama second.

    The result is advisory.  In particular, an unavailable model returns a
    fail-open ``proceed`` result so explicit downloads, paths, edits, trims,
    and other deterministic user requests are never disabled by AI availability.
    """

    name = str(operation or "").strip()
    model = str(params.get("agentic_model") or params.get("model") or "").strip()
    purpose = f"{name or 'operation'} request preflight"
    if not model:
        decision = OperationPreflightDecision(
            "proceed",
            "No global agentic model is configured; preserving the explicit request",
            model="",
            fallback=True,
        )
        _log("FALLBACK", decision, purpose)
        return decision

    evidence = _operation_evidence(name, params)
    messages = [
            {
                "role": "system",
                "content": (
                    "Audit the requested media-tool operation using only the supplied evidence. "
                    "Do not invent facts and do not propose replacements for any path, URL, link, "
                    "timestamp, title, or user setting. Return proceed when the explicit "
                    "request is "
                    "internally coherent. Return review only for a concrete ambiguity or conflict "
                    "visible in the evidence. This is an advisory audit; metadata changes are "
                    "validated separately by the metadata verifier. Keep the reason concise."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "evidence": evidence,
                        "policy": (
                            "Use only evidence fields. Values under immutable_references are exact "
                            "user inputs and must not be rewritten or reinterpreted."
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ]
    print(f"[AI-PREFLIGHT-START] {purpose} | model={model}")
    try:
        response = chat_json(messages, _SCHEMA, model=model, timeout=timeout)
        proposed = response.data
        decision = _validate_response(proposed, model)
        if not decision.fallback:
            try:
                reviewed = chat_json(
                    [
                        {
                            "role": "system",
                            "content": (
                                "Independently review another agent's media-operation preflight. "
                                "Use only supplied evidence, preserve all immutable references, "
                                "and report review only for a concrete conflict. Do not invent or "
                                "rewrite paths, URLs, timestamps, titles, or settings."
                            ),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "evidence": evidence,
                                    "first_agent_decision": proposed,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    _SCHEMA,
                    model=model,
                    timeout=timeout,
                ).data
                review_decision = _validate_response(reviewed, model)
                if not review_decision.fallback:
                    decision = review_decision
                    print(f"[AI-PREFLIGHT-REVIEWED] {purpose} | model={model}")
            except Exception as review_exc:
                print(
                    f"[AI-PREFLIGHT-REVIEW-FALLBACK] {purpose} | "
                    f"kept first audit: {review_exc}"
                )
    except Exception as exc:
        decision = OperationPreflightDecision(
            "proceed",
            f"AI preflight unavailable; preserving the explicit request: {exc}",
            model=model,
            fallback=True,
        )
    state = "FALLBACK" if decision.fallback else (
        "VERIFIED" if decision.action == "proceed" else "REVIEW"
    )
    _log(state, decision, purpose)
    return decision


def _validate_response(proposed: object, model: str) -> OperationPreflightDecision:
    if not isinstance(proposed, dict) or set(proposed) != set(_SCHEMA["required"]):
        return OperationPreflightDecision(
            "proceed",
            "Invalid AI preflight response; preserving the explicit request",
            model=model,
            fallback=True,
        )
    action = str(proposed.get("action") or "").strip().lower()
    reason = " ".join(str(proposed.get("reason") or "").split())
    raw_concerns = proposed.get("concerns")
    if action not in {"proceed", "review"} or not reason or not isinstance(raw_concerns, list):
        return OperationPreflightDecision(
            "proceed",
            "Malformed AI preflight response; preserving the explicit request",
            model=model,
            fallback=True,
        )
    if not all(isinstance(value, str) for value in raw_concerns):
        return OperationPreflightDecision(
            "proceed",
            "Malformed AI preflight concerns; preserving the explicit request",
            model=model,
            fallback=True,
        )
    concerns = tuple(
        cleaned
        for value in raw_concerns[:10]
        if (cleaned := " ".join(value.split()))
    )
    return OperationPreflightDecision(action, reason, concerns, model=model)


def _operation_evidence(operation: str, params: Mapping[str, object]) -> dict[str, Any]:
    references: list[dict[str, str]] = []
    _collect_references(params, "params", references)
    options: dict[str, object] = {}
    for key, value in params.items():
        normalized = str(key).casefold()
        if key in _MODEL_KEYS or _is_reference(normalized, value):
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            options[str(key)] = value
        elif isinstance(value, str):
            # Free-form text is context rather than an instruction to the agent.
            options[str(key)] = value[:500]
        elif isinstance(value, Mapping):
            options[str(key)] = {"kind": "mapping", "items": len(value)}
        elif isinstance(value, (list, tuple)):
            options[str(key)] = {"kind": "sequence", "items": len(value)}
    return {
        "operation": operation,
        "options": options,
        "immutable_references": references,
    }


def _collect_references(
    value: object,
    location: str,
    result: list[dict[str, str]],
    *,
    depth: int = 0,
) -> None:
    if depth > 5 or len(result) >= 100:
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            normalized = str(key).casefold()
            if _is_reference(normalized, child):
                if isinstance(child, (str, int, float)):
                    result.append({"field": child_location, "value": str(child)})
                elif isinstance(child, (list, tuple)):
                    for index, item in enumerate(child):
                        if isinstance(item, (str, int, float)) and len(result) < 100:
                            result.append(
                                {"field": f"{child_location}[{index}]", "value": str(item)}
                            )
            else:
                _collect_references(child, child_location, result, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value[:100]):
            _collect_references(child, f"{location}[{index}]", result, depth=depth + 1)


def _is_reference(key: str, value: object) -> bool:
    if any(marker in key for marker in _REFERENCE_MARKERS) or key in _TIMESTAMP_KEYS:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(
        text.startswith(("http://", "https://"))
        or re.match(r"^[A-Za-z]:[\\/]", text)
        or _TIMESTAMP_PATTERN.fullmatch(text)
    )


def _log(state: str, decision: OperationPreflightDecision, purpose: str) -> None:
    print(
        f"[AI-PREFLIGHT-{state}] {purpose} | model={decision.model or 'none'} "
        f"| action={decision.action} | {decision.reason}"
    )
