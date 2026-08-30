"""Small console helper invoked by Claude Code's statusLine setting."""

from __future__ import annotations

import json
import sys

from sentinel.claude_status import (
    ClaudeStatusLineIntegration,
    ClaudeStatusStore,
    render_statusline,
)


def main() -> int:
    if sys.argv[1:] == ["--unregister"]:
        ClaudeStatusLineIntegration().remove_if_owned()
        return 0
    if sys.argv[1:]:
        return 2
    raw = sys.stdin.read(1_000_001)
    if len(raw) > 1_000_000:
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 2
    if not ClaudeStatusStore().record_statusline(payload):
        return 2
    print(render_statusline(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
