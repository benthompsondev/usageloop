"""Current observation-only Codex app-server protocol client."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import json
import time
from typing import Any, Protocol


class LineTransport(Protocol):
    def start(self) -> None: ...
    def send_line(self, line: str) -> None: ...
    def read_line(self, timeout: float | None = None) -> str: ...
    def close(self) -> None: ...


class AppServerProtocolError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class AuthenticationUnavailableError(AppServerProtocolError):
    def __init__(self):
        super().__init__(
            "authentication_unavailable",
            "Subscription authentication is unavailable through the local Codex app-server.",
        )


#: JSON-RPC codes the app-server emits when it rejects a request before dispatching
#: it. Observed directly: sending an experimental-only thread parameter without the
#: `experimentalApi` capability returns -32600 and starts nothing.
PRE_DISPATCH_ERROR_CODES = frozenset({-32600, -32601, -32602})


class AppServerRequestRejected(AppServerProtocolError):
    """A method returned a JSON-RPC error object."""

    def __init__(self, method: str, error: dict[str, Any]):
        code = error.get("code")
        self.code = code if isinstance(code, int) else None
        self.method = method
        super().__init__("app_server_rejected", f"Codex rejected {method}.")

    @property
    def rejected_before_dispatch(self) -> bool:
        """True when Codex refused the request itself, so no model work began."""
        return self.code in PRE_DISPATCH_ERROR_CODES


def _extract_thread_id(result: dict[str, Any]) -> str | None:
    for candidate in (result.get("threadId"), result.get("id")):
        if isinstance(candidate, str) and candidate:
            return candidate
    thread = result.get("thread")
    if isinstance(thread, dict):
        for key in ("threadId", "id"):
            value = thread.get(key)
            if isinstance(value, str) and value:
                return value
    return None


@dataclass(frozen=True)
class ServerInfo:
    user_agent: str
    platform_family: str
    platform_os: str


class AppServerClient:
    def __init__(self, transport: LineTransport, *, client_version: str, timeout: float = 15.0):
        self.transport = transport
        self.client_version = client_version
        self.timeout = timeout
        self._next_id = 1
        self._rate_notifications: list[dict[str, Any]] = []
        self._turn_outcomes: list[str] = []
        self._initialized = False

    def initialize(self) -> ServerInfo:
        self.transport.start()
        request_id = self._request_id()
        self._send(
            {
                "method": "initialize",
                "id": request_id,
                "params": {
                    "clientInfo": {
                        "name": "codex-window-sentinel",
                        "title": "Codex Window Sentinel",
                        "version": self.client_version,
                    },
                    "capabilities": {
                        "experimentalApi": False,
                        "requestAttestation": False,
                    },
                },
            }
        )
        response = self._wait_for_response(request_id)
        result = response.get("result")
        if not isinstance(result, dict):
            raise AppServerProtocolError("protocol_unsupported", "Codex returned an unsupported initialize response.")
        required = ("userAgent", "platformFamily", "platformOs")
        if any(not isinstance(result.get(field), str) for field in required):
            raise AppServerProtocolError("protocol_unsupported", "Codex initialize metadata is malformed.")
        self._send({"method": "initialized"})
        self._initialized = True
        return ServerInfo(
            user_agent=result["userAgent"],
            platform_family=result["platformFamily"],
            platform_os=result["platformOs"],
        )

    def read_rate_limits(self) -> dict[str, Any]:
        if not self._initialized:
            raise AppServerProtocolError("not_initialized", "The Codex app-server handshake is incomplete.")
        request_id = self._request_id()
        self._send({"method": "account/rateLimits/read", "id": request_id, "params": None})
        response = self._wait_for_response(request_id)
        error = response.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and "authentication required" in message.lower():
                raise AuthenticationUnavailableError()
            raise AppServerProtocolError("rate_limits_unavailable", "Codex could not return subscription rate limits.")
        result = response.get("result")
        if not isinstance(result, dict) or not (
            isinstance(result.get("rateLimits"), dict)
            or isinstance(result.get("rateLimitsByLimitId"), dict)
        ):
            raise AppServerProtocolError("protocol_unsupported", "Codex returned an unsupported rate-limit response.")
        return result

    def list_models(self) -> list[dict[str, Any]]:
        """Read the installed runtime's model catalog. Sends no model request."""
        result = self._call("model/list", {})
        data = result.get("data")
        if not isinstance(data, list):
            raise AppServerProtocolError("protocol_unsupported", "Codex returned an unsupported model list.")
        return [entry for entry in data if isinstance(entry, dict)]

    def start_thread(self, params: dict[str, Any]) -> str:
        """Create a thread. Creating a thread does not start a turn or spend quota."""
        result = self._call("thread/start", params)
        thread_id = _extract_thread_id(result)
        if thread_id is None:
            raise AppServerProtocolError("protocol_unsupported", "Codex returned no usable thread identifier.")
        return thread_id

    def start_turn(self, params: dict[str, Any]) -> None:
        """Submit exactly one turn. This is the only method that can spend quota."""
        self._call("turn/start", params)

    def await_turn_end(self, *, timeout: float) -> str:
        """Wait for a bounded turn lifecycle signal. Diagnostic only, never the verdict."""
        if self._turn_outcomes:
            return self._turn_outcomes.pop(0)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "turn_timeout"
            try:
                line = self.transport.read_line(timeout=remaining)
            except Exception:
                return "turn_stream_unavailable"
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if method == "account/rateLimits/updated":
                params = message.get("params")
                if isinstance(params, dict) and isinstance(params.get("rateLimits"), dict):
                    self._rate_notifications.append(params)
                continue
            if method == "turn/completed":
                return "turn_completed"
            if method == "error":
                return "turn_error"

    def drain_rate_limit_notifications(self) -> list[dict[str, Any]]:
        notifications = self._rate_notifications
        self._rate_notifications = []
        return notifications

    def close(self) -> None:
        self.transport.close()
        self._initialized = False

    def _call(self, method: str, params: Any) -> dict[str, Any]:
        if not self._initialized:
            raise AppServerProtocolError("not_initialized", "The Codex app-server handshake is incomplete.")
        request_id = self._request_id()
        self._send({"method": method, "id": request_id, "params": params})
        response = self._wait_for_response(request_id)
        error = response.get("error")
        if isinstance(error, dict):
            raise AppServerRequestRejected(method, error)
        result = response.get("result")
        if not isinstance(result, dict):
            raise AppServerProtocolError("protocol_unsupported", f"Codex returned an unsupported {method} response.")
        return result

    def _request_id(self) -> int:
        value = self._next_id
        self._next_id += 1
        return value

    def _send(self, message: dict[str, Any]) -> None:
        self.transport.send_line(json.dumps(message, separators=(",", ":"), ensure_ascii=True))

    def _wait_for_response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerProtocolError("app_server_timeout", "The local Codex app-server did not respond in time.")
            line = self.transport.read_line(timeout=remaining)
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppServerProtocolError("invalid_json", "The local Codex app-server emitted malformed JSON.") from exc
            if not isinstance(message, dict):
                continue
            if message.get("method") == "account/rateLimits/updated":
                params = message.get("params")
                if isinstance(params, dict) and isinstance(params.get("rateLimits"), dict):
                    self._rate_notifications.append(params)
                continue
            if message.get("method") == "turn/completed":
                self._turn_outcomes.append("turn_completed")
                continue
            if message.get("method") == "error":
                self._turn_outcomes.append("turn_error")
                continue
            if message.get("id") == request_id:
                return message


def merge_sparse_rate_limits(
    current: dict[str, Any], notification_params: dict[str, Any]
) -> dict[str, Any]:
    """Merge non-null sparse fields without treating unavailable metadata as deletion."""
    merged = copy.deepcopy(current)
    update = notification_params.get("rateLimits")
    if not isinstance(update, dict):
        return merged

    update_limit_id = update.get("limitId")
    compatibility = merged.get("rateLimits")
    if isinstance(compatibility, dict):
        current_limit_id = compatibility.get("limitId")
        if update_limit_id in (None, "codex", current_limit_id):
            merged["rateLimits"] = _merge_non_null(compatibility, update)

    by_limit_id = merged.get("rateLimitsByLimitId")
    if isinstance(by_limit_id, dict) and isinstance(update_limit_id, str):
        existing = by_limit_id.get(update_limit_id)
        if isinstance(existing, dict):
            by_limit_id[update_limit_id] = _merge_non_null(existing, update)
        else:
            by_limit_id[update_limit_id] = copy.deepcopy(update)
    return merged


def _merge_non_null(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(current)
    for key, value in update.items():
        if value is None:
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_non_null(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged
