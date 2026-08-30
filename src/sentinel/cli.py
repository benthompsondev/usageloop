"""Command-line interface for observation and bounded Codex window triggers."""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time
from typing import Any, Sequence

from . import __version__
from .chain import ChainCoordinator, ChainPolicy, ChainResult
from .classifier import Classification, classify
from .history import SafeHistory, default_history_path
from .models import select_trigger_model
from .protocol import AppServerClient
from .quota import QuotaSnapshot, QuotaWindow, normalize_rate_limits, select_five_hour
from .transport import (
    CodexProcessTransport,
    SentinelRuntimeError,
    find_codex_executable,
    read_codex_version,
)
from .trigger import (
    AppServerTrigger,
    TriggerConfig,
    dedicated_trigger_workspace,
)


@dataclass
class RuntimeSession:
    executable: Path
    codex_version: str
    client: AppServerClient
    platform_os: str

    def close(self) -> None:
        self.client.close()


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="Observe whether the Codex five-hour subscription window is anchored.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="Verify local Codex app-server access safely.")

    status = commands.add_parser("status", help="Read current windows and classify recent evidence.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    sample = commands.add_parser("sample", help="Collect a short multi-observation evidence set.")
    sample.add_argument("--count", type=_minimum_three, default=4, help="Observation count (default: 4).")
    sample.add_argument("--interval", type=_positive_float, default=10.0, help="Seconds between reads (default: 10).")

    watch = commands.add_parser("watch", help="Continuously poll and print state transitions.")
    watch.add_argument("--interval", type=_positive_float, default=30.0, help="Polling interval in seconds (default: 30).")

    chain = commands.add_parser(
        "chain",
        help="Trigger one eligible post-rollover Codex window and verify it anchored.",
    )
    chain.add_argument("--dry-run", action="store_true", help="Evaluate and show the trigger plan without sending a request.")
    chain.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")

    bootstrap = commands.add_parser(
        "bootstrap",
        help="Explicitly start and verify a first five-hour Codex window.",
    )
    bootstrap.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm that one eligible minimal Codex request may be sent.",
    )
    bootstrap.add_argument("--dry-run", action="store_true", help="Evaluate eligibility without reserving or sending a request.")
    bootstrap.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser


def build_status_payload(
    snapshot: QuotaSnapshot,
    classification: Classification,
    codex_version: str,
) -> dict[str, Any]:
    selection = select_five_hour(snapshot)
    selected = selection.window
    other = [window for window in snapshot.windows if window is not selected]
    five_hour: dict[str, Any] = {
        "present": selection.status != "absent",
        "used_percent": selected.used_percent if selected else None,
        "duration_minutes": selected.duration_minutes if selected else None,
        "resets_at": _reset_iso(selected.resets_at) if selected else None,
        "remaining_seconds": _remaining_seconds(selected, snapshot.observed_at),
        "limit_id": selected.limit_id if selected else None,
        "state": classification.state,
        "confidence": classification.confidence,
        "reason": classification.reason,
        "evidence": classification.evidence,
    }
    return {
        "timestamp": _iso_timestamp(snapshot.observed_at),
        "sentinel_version": __version__,
        "codex_version": codex_version,
        "five_hour_window": five_hour,
        "other_windows": [_window_payload(window, snapshot.observed_at) for window in other],
    }


def connect() -> RuntimeSession:
    executable = find_codex_executable()
    codex_version = read_codex_version(executable)
    transport = CodexProcessTransport(executable)
    client = AppServerClient(transport, client_version=__version__)
    try:
        server = client.initialize()
    except Exception:
        client.close()
        raise
    return RuntimeSession(executable, codex_version, client, server.platform_os)


def run_doctor() -> int:
    session = connect()
    try:
        observed_at = time.time()
        snapshot = normalize_rate_limits(session.client.read_rate_limits(), observed_at)
        print(f"Sentinel: {__version__} on Python {platform.python_version()} ({platform.system()})")
        print(f"Codex executable: {session.executable}")
        print(f"Codex version: {session.codex_version}")
        print(f"App-server handshake: OK ({session.platform_os})")
        print(f"Subscription rate limits: available ({len(snapshot.windows)} window(s))")
        print("Trigger transport: local app-server turn (thread/start + turn/start)")
        print(f"Trigger model: {_describe_trigger_model(session)}")
        print(f"Safe log: {default_history_path()}")
        print("Doctor boundary: read-only checks only; no model request was sent.")
        return 0
    finally:
        session.close()


def _describe_trigger_model(session: RuntimeSession) -> str:
    """Read-only preview of the model a trigger would resolve. Sends no request."""
    try:
        choice = select_trigger_model(session.client.list_models())
    except Exception:
        return "unavailable (model/list failed)"
    if choice is None:
        return "none usable (every visible model is superseded)"
    effort = choice.reasoning_effort or "provider default"
    return f"{choice.model} / {effort}"


def run_status(*, json_output: bool) -> int:
    history = SafeHistory()
    session = connect()
    try:
        snapshot, notification_count = _read_snapshot(session)
        recent = history.load_recent(now=snapshot.observed_at, limit=3)
        classification = classify([*recent, snapshot])
        if notification_count:
            classification.evidence["notification_count"] = notification_count
        history.record_observation(snapshot, classification, session.codex_version)
        payload = build_status_payload(snapshot, classification, session.codex_version)
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(_format_status(payload))
        return 0
    finally:
        session.close()


def run_sample(*, count: int, interval: float) -> int:
    history = SafeHistory()
    session = connect()
    observations: list[QuotaSnapshot] = []
    notification_count = 0
    try:
        for index in range(count):
            snapshot, received = _read_snapshot(session)
            observations.append(snapshot)
            notification_count += received
            selection = select_five_hour(snapshot)
            reset = _reset_iso(selection.window.resets_at) if selection.window else "not exposed"
            print(f"[{index + 1}/{count}] observed {reset}")
            if index + 1 < count:
                time.sleep(interval)
        classification = classify(observations)
        if notification_count:
            classification.evidence["notification_count"] = notification_count
        for snapshot in observations:
            history.record_observation(snapshot, classification, session.codex_version)
        print()
        print(_format_status(build_status_payload(observations[-1], classification, session.codex_version)))
        return 0
    finally:
        session.close()


def run_watch(*, interval: float) -> int:
    history = SafeHistory()
    observations: deque[QuotaSnapshot] = deque(maxlen=4)
    previous_state: str | None = None
    backoff = 2.0
    print(f"Watching every {interval:g}s. Press Ctrl+C to stop.")
    while True:
        session: RuntimeSession | None = None
        try:
            session = connect()
            backoff = 2.0
            while True:
                snapshot, notification_count = _read_snapshot(session)
                observations.append(snapshot)
                classification = classify(list(observations))
                if notification_count:
                    classification.evidence["notification_count"] = notification_count
                history.record_observation(snapshot, classification, session.codex_version)
                if classification.state != previous_state:
                    stamp = _iso_timestamp(snapshot.observed_at)
                    old = previous_state or "START"
                    print(f"{stamp}  {old} -> {classification.state}: {classification.reason}")
                    if previous_state is not None:
                        history.record_transition(previous_state, classification.state)
                    previous_state = classification.state
                time.sleep(interval)
        except KeyboardInterrupt:
            print("Stopped.")
            return 0
        except Exception as exc:
            category = _error_category(exc)
            history.record_error(category)
            print(f"Observation unavailable ({category}); reconnecting in {backoff:g}s.", file=sys.stderr)
            try:
                time.sleep(backoff)
            except KeyboardInterrupt:
                print("Stopped.")
                return 0
            backoff = min(30.0, backoff * 2)
        finally:
            if session is not None:
                session.close()


def run_chain(*, dry_run: bool, json_output: bool) -> int:
    return _run_trigger_command(
        mode="rollover",
        dry_run=dry_run,
        json_output=json_output,
        confirmed=True,
    )


def run_bootstrap(*, confirmed: bool, dry_run: bool, json_output: bool) -> int:
    if not confirmed and not dry_run:
        message = "Bootstrap requires explicit opt-in. Re-run with --confirm to allow one eligible request."
        if json_output:
            print(json.dumps({"status": "CONSENT_REQUIRED", "reason": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 3
    return _run_trigger_command(
        mode="bootstrap",
        dry_run=dry_run,
        json_output=json_output,
        confirmed=confirmed,
    )


def _run_trigger_command(
    *, mode: str, dry_run: bool, json_output: bool, confirmed: bool
) -> int:
    history = SafeHistory()
    session = connect()
    output = sys.stderr if json_output else sys.stdout
    trigger = AppServerTrigger(
        session.client,
        dedicated_trigger_workspace(history.path),
        TriggerConfig(),
    )

    def collect(label: str) -> list[QuotaSnapshot]:
        observations: list[QuotaSnapshot] = []
        for index in range(4):
            snapshot, _notification_count = _read_snapshot(session)
            observations.append(snapshot)
            print(f"{label} [{index + 1}/4]", file=output)
            if index < 3:
                time.sleep(10.0)
        classification = classify(observations)
        for snapshot in observations:
            history.record_observation(snapshot, classification, session.codex_version)
        return observations

    try:
        preflight = collect("Preflight")
        coordinator = ChainCoordinator(trigger, history, ChainPolicy())
        if mode == "bootstrap":
            result = coordinator.run_bootstrap(
                preflight,
                lambda: collect("Verification"),
                confirmed=confirmed,
                dry_run=dry_run,
            )
        else:
            result = coordinator.run(
                preflight,
                lambda: collect("Verification"),
                dry_run=dry_run,
            )
        if json_output:
            payload = result.to_dict()
            payload["codex_version"] = session.codex_version
            payload["sentinel_version"] = __version__
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print()
            print(_format_chain(result))
        return _chain_exit_code(result)
    finally:
        session.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return run_doctor()
        if args.command == "status":
            return run_status(json_output=args.json)
        if args.command == "sample":
            return run_sample(count=args.count, interval=args.interval)
        if args.command == "watch":
            return run_watch(interval=args.interval)
        if args.command == "chain":
            return run_chain(dry_run=args.dry_run, json_output=args.json)
        if args.command == "bootstrap":
            return run_bootstrap(
                confirmed=args.confirm,
                dry_run=args.dry_run,
                json_output=args.json,
            )
        raise AssertionError("unreachable command")
    except KeyboardInterrupt:
        print("Stopped.")
        return 130
    except Exception as exc:
        category = _error_category(exc)
        SafeHistory().record_error(category)
        print(f"Sentinel could not complete the observation ({category}).", file=sys.stderr)
        return 2


def _read_snapshot(session: RuntimeSession) -> tuple[QuotaSnapshot, int]:
    observed_at = time.time()
    payload = session.client.read_rate_limits()
    notifications = session.client.drain_rate_limit_notifications()
    return normalize_rate_limits(payload, observed_at), len(notifications)


def _window_payload(window: QuotaWindow, observed_at: float) -> dict[str, Any]:
    return {
        "limit_id": window.limit_id,
        "used_percent": window.used_percent,
        "duration_minutes": window.duration_minutes,
        "resets_at": _reset_iso(window.resets_at),
        "remaining_seconds": _remaining_seconds(window, observed_at),
        "blocked_reason": window.blocked_reason,
    }


def _format_status(payload: dict[str, Any]) -> str:
    window = payload["five_hour_window"]
    lines = [
        f"Observed: {payload['timestamp']}",
        f"Codex: {payload['codex_version']}",
        f"Five-hour state: {window['state']} ({window['confidence']})",
        f"Why: {window['reason']}",
    ]
    if window["present"] and window["duration_minutes"] is not None:
        lines.extend(
            [
                f"Usage: {window['used_percent']}%",
                f"Window: {window['duration_minutes']} minutes",
                f"Resets at: {window['resets_at']}",
                f"Remaining: {_human_duration(window['remaining_seconds'])}",
            ]
        )
    else:
        lines.append("Five-hour window: not uniquely exposed")
    other = payload["other_windows"]
    if other:
        lines.append("Other windows:")
        for item in other:
            lines.append(
                f"  {item['duration_minutes']} min | {item['used_percent']}% used | resets {item['resets_at']}"
            )
    evidence = window["evidence"]
    if evidence:
        lines.append("Evidence: " + json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return "\n".join(lines)


def _format_chain(result: ChainResult) -> str:
    description = result.trigger
    lines = [
        f"Chain result: {result.status}",
        f"Why: {result.reason}",
        f"Anchored: {'yes' if result.anchored else 'no'}",
        f"Request possibly sent: {'yes' if result.request_possibly_sent else 'no'}",
        f"Classifier: {result.classification.state} ({result.classification.confidence})",
        "Trigger: one app-server turn (thread/start + turn/start)",
        f"Model/reasoning: {description.model} / {description.reasoning_effort} (resolved from model/list)",
        f"Minimal input: {description.prompt_characters} characters (contents are not logged)",
    ]
    if result.terminal_outcome is not None:
        lines.append(f"Turn lifecycle outcome: {result.terminal_outcome} (diagnostic only)")
    if result.attempt_state is not None:
        lines.append(f"Persisted attempt state: {result.attempt_state}")
    if result.mode == "bootstrap":
        lines.append("Retry bound: one full five-hour cooldown after any possibly sent request")
    else:
        lines.append("Retry bound: one possibly sent request per observed rollover boundary")
    if result.boundary_reset_at is not None:
        lines.append(f"Rollover boundary: {_reset_iso(result.boundary_reset_at)}")
    return "\n".join(lines)


def _chain_exit_code(result: ChainResult) -> int:
    if result.status in {"ANCHOR_VERIFIED", "ALREADY_ANCHORED", "DRY_RUN"}:
        return 0
    if result.status in {
        "NOT_ELIGIBLE",
        "EVIDENCE_TOO_WEAK",
        "CONSENT_REQUIRED",
        "BOOTSTRAP_USAGE_UNSUITABLE",
        "BOOTSTRAP_COOLDOWN",
        "WEEKLY_UNAVAILABLE",
        "WEEKLY_EXHAUSTED",
        "ROLLOVER_BOUNDARY_UNKNOWN",
        "RESET_BUFFER",
        "ATTEMPT_ALREADY_RECORDED",
    }:
        return 3
    return 4


def _remaining_seconds(window: QuotaWindow | None, observed_at: float) -> int | None:
    if window is None or window.resets_at is None:
        return None
    return max(0, int(window.resets_at - observed_at))


def _reset_iso(value: int | None) -> str | None:
    return _iso_timestamp(value) if value is not None else None


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _human_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes}m {secs}s"


def _error_category(exc: Exception) -> str:
    category = getattr(exc, "category", None)
    return category if isinstance(category, str) else "unexpected_error"


def _minimum_three(value: str) -> int:
    parsed = int(value)
    if parsed < 3:
        raise argparse.ArgumentTypeError("count must be at least 3")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than zero")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
