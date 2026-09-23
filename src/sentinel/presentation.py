"""One side-effect-free decision for the dashboard, next action, and tray."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .app_state import AppSettings, ProviderViewState, format_countdown
from .product import PRODUCT
from .schedule import DAILY, WEEKLY, schedule_summary


@dataclass(frozen=True)
class OperationalPresentation:
    kind: str
    banner_title: str
    banner_detail: str
    tone: str
    card_status: str
    card_headline: str
    card_detail: str
    next_action: str
    manual_start_visible: bool
    tray_text: str


def operational_presentation(
    settings: AppSettings, state: ProviderViewState | None, *, now: float,
    checking: bool = False, persistence_error: str | None = None,
) -> OperationalPresentation:
    prefix = f"{PRODUCT.display_name} · "

    def make(kind, title, detail, tone, card_status, headline, card_detail, next_action,
             manual=False, tray=None):
        return OperationalPresentation(kind, title, detail, tone, card_status,
                                       headline, card_detail, next_action, manual,
                                       prefix + (tray or title))

    if persistence_error is not None:
        return make("needs_attention", "UsageLoop needs attention",
                    "Local state could not be saved. Automatic starts are paused.",
                    "warning", "NEEDS ATTENTION", "Local state unavailable",
                    "A local setting or history record could not be saved safely.",
                    "Start blocked until local state can be saved.", tray="Needs attention")
    if settings.pause_active(now):
        return make("paused", "Automation is temporarily paused",
                    "Your saved routine resumes " + _absolute_time(settings.automation_paused_until) + ".",
                    "info", "PAUSED",
                    "Routine paused", "No automatic start during this pause.",
                    "Resumes " + _absolute_time(settings.automation_paused_until),
                    tray="Paused until " + _absolute_time(settings.automation_paused_until))
    if state is None:
        if not settings.automation_enabled and not settings.first_run_complete:
            return make("first_run", "Set up your routine",
                        "Choose when your day starts in Settings, then turn on Automation. Nothing is sent to Codex until you do.",
                        "info", "GET STARTED", "No clock running yet",
                        "Choose a start time, then turn on Automation.",
                        "Choose your start time in Settings, then turn on Automation",
                        tray="Set up your routine")
        return make("first_run", "Waiting for Codex status",
                    "UsageLoop has not checked Codex yet.", "info", "NOT CHECKED",
                    "No reset clock verified yet", "Sync usage to read the current window.",
                    "Sync usage to check the current window", tray="Waiting for Codex status")
    if not state.installed:
        return make("needs_attention", "Codex needs attention",
                    "Install and sign in to Codex before UsageLoop can check a window.",
                    "warning", "NEEDS ATTENTION", "Codex not found",
                    "No local Codex runtime is available.", "Start blocked until Codex is available.",
                    tray="Needs attention")
    if checking or state.status == "Checking":
        return make("checking", "Checking the Codex connection",
                    "Reading usage and supported models. No model request is sent.",
                    "info", "CHECKING CONNECTION", "Checking Codex",
                    "Compatibility is being checked without sending a turn.",
                    "Wait for the read-only check.", tray="Checking Codex")
    if (not settings.automation_enabled and not settings.first_run_complete
            and state.provider_id not in settings.checked_runtime_identities
            and state.usage_checked_at is None and state.last_verified_at is None
            and state.reset_at is None
            and state.status != "Needs attention"):
        return make("first_run", "Set up your routine",
                    "Choose when your day starts in Settings, then turn on Automation. Nothing is sent to Codex until you do.",
                    "info", "GET STARTED", "No clock running yet",
                    "Choose a start time, then turn on Automation.",
                    "Choose your start time in Settings, then turn on Automation",
                    tray="Set up your routine")
    if (state.compatibility_next_retry_at is None and
            (state.status == "Needs attention" or not state.automation_supported
             or (settings.checked_runtime_identities.get(state.provider_id) == state.runtime_identity
                 and settings.compatible_runtime_identities.get(state.provider_id) != state.runtime_identity))):
        return make("needs_attention", "UsageLoop stopped safely", state.detail,
                    "warning", "NEEDS ATTENTION", "Start blocked", state.detail,
                    "Start blocked until Codex passes a compatibility check.",
                    tray="Needs attention")
    if not settings.automation_enabled:
        headline = (format_countdown(state.reset_at, now)
                    if state.reset_at is not None and state.reset_at > now else "Automation off")
        return make("automation_off", "Automation is off",
                    "No automatic starts. Your saved routine remains available.",
                    "info", "AUTOMATION OFF", headline,
                    "No automatic Codex request will be sent.",
                    "No automatic requests while automation is off", tray="Automation off")
    if state.compatibility_next_retry_at is not None:
        return make("reconnecting", "Reconnecting to Codex",
                    "UsageLoop is retrying read-only compatibility checks.",
                    "info", "RECONNECTING", "Checking Codex again",
                    "No model request is sent during recovery.",
                    "Start blocked while Codex reconnects. Next check " +
                    _time(state.compatibility_next_retry_at, now), tray="Reconnecting to Codex")
    if settings.compatible_runtime_identities.get(state.provider_id) != state.runtime_identity:
        return make("checking", "Checking the Codex connection",
                    "UsageLoop must confirm this Codex version before a start.",
                    "info", "CHECKING CONNECTION", "Compatibility not checked",
                    "A read-only compatibility check is needed.",
                    "Start blocked until Codex passes a compatibility check.",
                    tray="Checking Codex")
    if state.outcome_category == "GUARDED" or state.last_action in {
        "ANCHOR_NOT_VERIFIED", "VERIFICATION_UNAVAILABLE", "BOOTSTRAP_COOLDOWN",
        "ATTEMPT_ALREADY_RECORDED",
    }:
        return make("guarded", "Start outcome is unconfirmed",
                    "A request may have been sent. UsageLoop will not retry that attempt.",
                    "warning", "NO RETRY", "Start not confirmed", state.detail,
                    "No retry for the uncertain start. Check Recent starts.", tray="Start not confirmed")
    if state.weekly_used_percent is not None and state.weekly_used_percent >= 99:
        return make("weekly_protected", "Weekly allowance protected",
                    "UsageLoop will not start another window while weekly use is this high.",
                    "warning", "WEEKLY PROTECTED", "Weekly limit near its end",
                    "The weekly safety check is blocking starts.",
                    "Automatic starts wait for weekly allowance to recover.",
                    tray="Weekly allowance protected")
    if state.quota_state == "ABSENT" and state.quota_evidence == "valid_weekly_only" and state.weekly_used_percent is not None:
        detail = "Codex isn't reporting a five-hour window right now. There's no five-hour start to schedule."
        return make("no_five_hour", "No five-hour start to schedule", detail,
                    "info", "NO 5-HOUR WINDOW", "No five-hour window", detail,
                    "No five-hour start is available to schedule.", tray="No five-hour window")
    if state.usage_checked_at is not None and state.weekly_used_percent is None:
        return make("weekly_unavailable", "Weekly evidence unavailable",
                    "UsageLoop cannot confirm the weekly safety check, so starts remain blocked.",
                    "warning", "WEEKLY NOT CHECKED", "Weekly check unavailable",
                    "Sync usage to get a valid weekly reading.",
                    "Start blocked until weekly evidence is available.", tray="Weekly check unavailable")
    if state.quota_state in {"UNKNOWN", "ABSENT"} and state.usage_checked_at is not None:
        return make("needs_attention", "Codex usage needs a check",
                    "The quota readings are inconsistent or cannot be interpreted safely.",
                    "warning", "NEEDS ATTENTION", "Usage unclear",
                    "Sync usage to read the current state.",
                    "Start blocked until quota evidence is clear.", tray="Needs attention")
    if (state.reset_at is not None and state.reset_at > now
            and state.quota_state != "UNANCHORED"
            and (state.quota_state == "ANCHORED" or state.status == "Ready")):
        return make("active_window", "Everything is set",
                    "The countdown runs locally. UsageLoop follows your saved schedule after this window ends.",
                    "success", "CLOCK RUNNING", format_countdown(state.reset_at, now),
                    "Codex confirmed this reset time. No Codex traffic is needed for the countdown.",
                    _active_window_next_action(settings, state.reset_at, now),
                    tray=format_countdown(state.reset_at, now) + " left")
    if (state.quota_state == "UNANCHORED" and state.last_verified_at is None
            and state.weekly_used_percent is not None
            and (state.reset_at is None or state.reset_at > now)):
        return make("first_window", "Ready when you choose to start",
                    "Starting a first window requires your approval.", "info",
                    "FIRST START", "No clock running yet",
                    "The first start uses a guarded Codex request after you approve it.",
                    "First window starts only when you ask", manual=True,
                    tray="Waiting for first window")
    if state.reset_at is not None:
        try:
            summary = schedule_summary(settings.schedule_mode, boundary_reset_at=state.reset_at,
                now=now, hour=settings.daily_start_hour, minute=settings.daily_start_minute,
                weekly_times=settings.weekly_start_times)
        except (OSError, OverflowError, ValueError):
            return make("needs_attention", "Schedule needs attention", "The saved schedule cannot be read safely.",
                        "warning", "NEEDS ATTENTION", "Schedule unavailable", state.detail,
                        "Start blocked until the saved schedule is usable.", tray="Needs attention")
        if summary.phase == "overnight_pause":
            next_action = "Overnight pause · first start " + _time(summary.next_action_at, now)
            return make("overnight_pause", "Overnight pause",
                        "Your routine is waiting for its next first start.", "info",
                        "OVERNIGHT PAUSE", "Waiting for first start",
                        "The previous window ended. Your saved routine is paused overnight.",
                        next_action, tray=next_action)
        if not summary.due:
            next_action = "Waiting for scheduled start " + _time(summary.next_action_at, now)
            return make("waiting", "Waiting for your scheduled start",
                        "The previous window ended. UsageLoop will follow your saved time.",
                        "info", "WAITING", "Waiting for saved start",
                        "Last checked quota remains visible below.", next_action,
                        tray=next_action)
        return make("due", "Safety checks are due",
                    "UsageLoop is checking whether the next start is safe.",
                    "info", "CHECKING", "Next window due",
                    "A start still needs fresh quota and history checks.",
                    "Safety checks are due now", tray="Next window due now")
    if state.status not in {"Ready", "Waiting", "Detected"}:
        return make("needs_attention", "Status unavailable", "Codex status needs a fresh check.",
                    "warning", "NEEDS ATTENTION", "Status unavailable",
                    "Sync usage to check Codex again.", "Sync usage to check Codex",
                    tray="Status unavailable")
    return make("first_run", "Waiting for a verified Codex window",
                "Sync usage to read the current state. A first start needs your approval.",
                "info", "NOT CHECKED", "No reset clock verified yet",
                "Read-only usage evidence is needed before a start.",
                "Sync usage to check the current window", tray="Waiting for Codex status")


def _active_window_next_action(settings: AppSettings, reset_at: float, now: float) -> str:
    try:
        summary = schedule_summary(
            settings.schedule_mode, boundary_reset_at=reset_at, now=now,
            hour=settings.daily_start_hour, minute=settings.daily_start_minute,
            weekly_times=settings.weekly_start_times,
        )
    except (OSError, OverflowError, ValueError):
        return "Next start waits for this window to reset and a usable schedule"
    target = _time(summary.next_action_at, now)
    if target == "when the schedule allows":
        return "Next start waits for this window to reset and a usable schedule"
    if summary.phase == "overnight_pause":
        return "After this window: overnight pause · first start " + target
    if summary.phase == "scheduled_first_start":
        return "Next start " + target
    return "Next start around " + target + ", after this window resets"


def _time(timestamp: float | None, now: float) -> str:
    if timestamp is None:
        return "when the schedule allows"
    try:
        target = datetime.fromtimestamp(timestamp)
        today = datetime.fromtimestamp(now).date()
    except (OSError, OverflowError, ValueError):
        return "when the schedule allows"
    clock = target.strftime("%I:%M %p").lstrip("0")
    if target.date() == today:
        return f"today at {clock}"
    if target.date() == today + timedelta(days=1):
        return f"tomorrow at {clock}"
    return target.strftime("%a, %b %d at %I:%M %p").replace(" 0", " ")


def _absolute_time(timestamp: float | None) -> str:
    if timestamp is None:
        return "the saved time"
    try:
        return datetime.fromtimestamp(timestamp).strftime(
            "%a, %b %d, %Y at %I:%M %p").replace(" 0", " ")
    except (OSError, OverflowError, ValueError):
        return "the saved time"
