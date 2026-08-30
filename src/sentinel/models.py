"""Dynamic Codex model selection for the bounded trigger.

Sentinel never persists a model name. The installed runtime is the authority, so
the trigger resolves a model from `model/list` immediately before every request.
This avoids the deprecation interstitials that a hardcoded name eventually earns:
the catalog marks a superseded model with a non-null `upgrade` pointer, which is
the same signal the Codex UI uses to demand a switch.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Sequence


_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_SAFE_EFFORT = re.compile(r"^[a-z]{1,16}$")


@dataclass(frozen=True)
class ModelChoice:
    model: str
    reasoning_effort: str | None
    is_default: bool


def select_trigger_model(catalog: Sequence[Any]) -> ModelChoice | None:
    """Pick the visible, current default model advertised by the installed runtime.

    Returns `None` when nothing is usable, which callers must treat as a refusal
    to trigger rather than a reason to guess a name.
    """
    usable = [entry for entry in catalog if _is_usable(entry)]
    if not usable:
        return None
    defaults = [entry for entry in usable if entry.get("isDefault") is True]
    if len(defaults) != 1:
        return None
    chosen = defaults[0]
    return ModelChoice(
        model=chosen["id"],
        reasoning_effort=_select_effort(chosen),
        is_default=chosen.get("isDefault") is True,
    )


def _is_usable(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not _SAFE_MODEL.fullmatch(identifier):
        return False
    if entry.get("hidden") is True:
        return False
    # A non-null `upgrade` means the runtime already considers this model
    # superseded and will interrupt to offer its replacement.
    return entry.get("upgrade") in (None, "")


def _select_effort(entry: dict[str, Any]) -> str | None:
    advertised = [
        item.get("reasoningEffort")
        for item in entry.get("supportedReasoningEfforts") or []
        if isinstance(item, dict)
    ]
    advertised = [value for value in advertised if isinstance(value, str) and _SAFE_EFFORT.fullmatch(value)]
    if "low" in advertised:
        return "low"
    default = entry.get("defaultReasoningEffort")
    if isinstance(default, str) and default in advertised:
        return default
    return advertised[0] if len(advertised) == 1 else None
