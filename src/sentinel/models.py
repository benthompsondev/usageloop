"""Resolve a low-cost anchor model from the current Codex catalog.

The catalog has availability and reasoning options, but no comparable quota
prices. Keep a short reviewed preference list, intersect it with live supported
models, and refuse unknown replacements rather than inherit an expensive default.
Historical selections are logged; they are never reused as configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Sequence


_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SAFE_EFFORT = re.compile(r"^[a-z]{1,16}$")
TRIGGER_MODEL_PREFERENCE = ("gpt-5.6-luna", "gpt-5.4-mini")
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


@dataclass(frozen=True)
class ModelChoice:
    model: str
    reasoning_effort: str | None
    is_default: bool


def select_trigger_model(catalog: Sequence[Any]) -> ModelChoice | None:
    """Prefer known lightweight models; no default-model or upgrade-pointer fallback."""
    usable = [entry for entry in catalog if _is_usable(entry)]
    if not usable:
        return None
    for model in TRIGGER_MODEL_PREFERENCE:
        matches = [entry for entry in usable if entry.get("model", entry["id"]) == model]
        if len(matches) > 1:
            return None
        if matches:
            chosen = matches[0]
            effort = _select_effort(chosen)
            if effort is None:
                return None
            return ModelChoice(model, effort, chosen.get("isDefault") is True)
    return None


def _is_usable(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not _SAFE_MODEL.fullmatch(identifier):
        return False
    if entry.get("hidden") is True:
        return False
    modalities = entry.get("inputModalities", ["text", "image"])
    if not isinstance(modalities, list) or "text" not in modalities:
        return False
    # A non-null `upgrade` means the runtime already considers this model
    # superseded and will interrupt to offer its replacement.
    return entry.get("upgrade") in (None, "") and entry.get("upgradeInfo") is None


def _select_effort(entry: dict[str, Any]) -> str | None:
    options = entry.get("supportedReasoningEfforts")
    if not isinstance(options, list):
        return None
    advertised = [
        item.get("reasoningEffort")
        for item in options
        if isinstance(item, dict)
    ]
    advertised = [value for value in advertised if isinstance(value, str) and _SAFE_EFFORT.fullmatch(value)]
    return next((effort for effort in _EFFORT_ORDER if effort in advertised), None)
