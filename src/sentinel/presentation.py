"""One side-effect-free decision for the dashboard, next action, and tray."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from .i18n import format_datetime, tr

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
    prefix = f'{PRODUCT.display_name} · '

    def make(kind, title, detail, tone, card_status, headline, card_detail, next_action,
             manual=False, tray=None):
        return OperationalPresentation(kind, tr(title), tr(detail), tone, tr(card_status),
                                       tr(headline), tr(card_detail), tr(next_action), manual,
                                       prefix + tr(tray or title))

    if persistence_error is not None:
        return make("needs_attention", tr("UsageLoop needs attention"),
                    tr("Local state could not be saved. Automatic starts are paused."),
                    "warning", tr("NEEDS ATTENTION"), tr("Local state unavailable"),
                    tr("A local setting or history record could not be saved safely."),
                    tr("Start blocked until local state can be saved."), tray=tr("Needs attention"))
    if settings.pause_active(now):
        return make("paused", tr("Automation is temporarily paused"),
                    tr("Your saved routine resumes ") + _absolute_time(settings.automation_paused_until) + ".",
                    "info", tr("PAUSED"),
                    tr("Routine paused"), tr("No automatic start during this pause."),
                    tr("Resumes ") + _absolute_time(settings.automation_paused_until),
                    tray=tr("Paused until ") + _absolute_time(settings.automation_paused_until))
    if state is None:
        if not settings.automation_enabled and not settings.first_run_complete:
            return make("first_run", tr("Set up your routine"),
                        tr("Choose when your day starts in Settings, then turn on Automation. Nothing is sent to Codex until you do."),
                        "info", tr("GET STARTED"), tr("No clock running yet"),
                        tr("Choose a start time, then turn on Automation."),
                        tr("Choose your start time in Settings, then turn on Automation"),
                        tray=tr("Set up your routine"))
        return make("first_run", tr("Waiting for Codex status"),
                    tr("UsageLoop has not checked Codex yet."), "info", tr("NOT CHECKED"),
                    tr("No reset clock verified yet"), tr("Sync usage to read the current window."),
                    tr("Sync usage to check the current window"), tray=tr("Waiting for Codex status"))
    if not state.installed:
        return make("needs_attention", tr("Codex needs attention"),
                    tr("Install and sign in to Codex before UsageLoop can check a window."),
                    "warning", tr("NEEDS ATTENTION"), tr("Codex not found"),
                    tr("No local Codex runtime is available."), tr("Start blocked until Codex is available."),
                    tray=tr("Needs attention"))
    if checking or state.status == "Checking":
        return make("checking", tr("Checking the Codex connection"),
                    tr("Reading usage and supported models. No model request is sent."),
                    "info", tr("CHECKING CONNECTION"), tr("Checking Codex"),
                    tr("Compatibility is being checked without sending a turn."),
                    tr("Wait for the read-only check."), tray=tr("Checking Codex"))
    if (not settings.automation_enabled and not settings.first_run_complete
            and state.provider_id not in settings.checked_runtime_identities
            and state.usage_checked_at is None and state.last_verified_at is None
            and state.reset_at is None
            and state.status != "Needs attention"):
        return make("first_run", tr("Set up your routine"),
                    tr("Choose when your day starts in Settings, then turn on Automation. Nothing is sent to Codex until you do."),
                    "info", tr("GET STARTED"), tr("No clock running yet"),
                    tr("Choose a start time, then turn on Automation."),
                    tr("Choose your start time in Settings, then turn on Automation"),
                    tray=tr("Set up your routine"))
    if (state.compatibility_next_retry_at is None and
            (state.status == "Needs attention" or not state.automation_supported
             or (settings.checked_runtime_identities.get(state.provider_id) == state.runtime_identity
                 and settings.compatible_runtime_identities.get(state.provider_id) != state.runtime_identity))):
        return make("needs_attention", tr("UsageLoop stopped safely"), state.detail,
                    "warning", tr("NEEDS ATTENTION"), tr("Start blocked"), state.detail,
                    tr("Start blocked until Codex passes a compatibility check."),
                    tray=tr("Needs attention"))
    if not settings.automation_enabled:
        headline = (format_countdown(state.reset_at, now)
                    if state.reset_at is not None and state.reset_at > now else tr("Automation off"))
        return make("automation_off", tr("Automation is off"),
                    tr("No automatic starts. Your saved routine remains available."),
                    "info", tr("AUTOMATION OFF"), headline,
                    tr("No automatic Codex request will be sent."),
                    tr("No automatic requests while automation is off"), tray=tr("Automation off"))
    if state.compatibility_next_retry_at is not None:
        return make("reconnecting", tr("Reconnecting to Codex"),
                    tr("UsageLoop is retrying read-only compatibility checks."),
                    "info", tr("RECONNECTING"), tr("Checking Codex again"),
                    tr("No model request is sent during recovery."),
                    tr("Start blocked while Codex reconnects. Next check ") +
                    _time(state.compatibility_next_retry_at, now), tray=tr("Reconnecting to Codex"))
    if settings.compatible_runtime_identities.get(state.provider_id) != state.runtime_identity:
        return make("checking", tr("Checking the Codex connection"),
                    tr("UsageLoop must confirm this Codex version before a start."),
                    "info", tr("CHECKING CONNECTION"), tr("Compatibility not checked"),
                    tr("A read-only compatibility check is needed."),
                    tr("Start blocked until Codex passes a compatibility check."),
                    tray=tr("Checking Codex"))
    if state.outcome_category == "GUARDED" or state.last_action in {
        "ANCHOR_NOT_VERIFIED", "VERIFICATION_UNAVAILABLE", "BOOTSTRAP_COOLDOWN",
        "ATTEMPT_ALREADY_RECORDED",
    }:
        return make("guarded", tr("Start outcome is unconfirmed"),
                    tr("A request may have been sent. UsageLoop will not retry that attempt."),
                    "warning", tr("NO RETRY"), tr("Start not confirmed"), state.detail,
                    tr("No retry for the uncertain start. Check Recent starts."), tray=tr("Start not confirmed"))
    if state.weekly_used_percent is not None and state.weekly_used_percent >= 99:
        return make("weekly_protected", tr("Weekly allowance protected"),
                    tr("UsageLoop will not start another window while weekly use is this high."),
                    "warning", tr("WEEKLY PROTECTED"), tr("Weekly limit near its end"),
                    tr("The weekly safety check is blocking starts."),
                    tr("Automatic starts wait for weekly allowance to recover."),
                    tray=tr("Weekly allowance protected"))
    if state.quota_state == "ABSENT" and state.quota_evidence == "valid_weekly_only" and state.weekly_used_percent is not None:
        detail = tr("Codex isn't reporting a five-hour window right now. There's no five-hour start to schedule.")
        return make("no_five_hour", tr("No five-hour start to schedule"), detail,
                    "info", tr("NO 5-HOUR WINDOW"), tr("No five-hour window"), detail,
                    tr("No five-hour start is available to schedule."), tray=tr("No five-hour window"))
    if state.usage_checked_at is not None and state.weekly_used_percent is None:
        return make("weekly_unavailable", tr("Weekly evidence unavailable"),
                    tr("UsageLoop cannot confirm the weekly safety check, so starts remain blocked."),
                    "warning", tr("WEEKLY NOT CHECKED"), tr("Weekly check unavailable"),
                    tr("Sync usage to get a valid weekly reading."),
                    tr("Start blocked until weekly evidence is available."), tray=tr("Weekly check unavailable"))
    if state.quota_state in {"UNKNOWN", "ABSENT"} and state.usage_checked_at is not None:
        return make("needs_attention", tr("Codex usage needs a check"),
                    tr("The quota readings are inconsistent or cannot be interpreted safely."),
                    "warning", tr("NEEDS ATTENTION"), tr("Usage unclear"),
                    tr("Sync usage to read the current state."),
                    tr("Start blocked until quota evidence is clear."), tray=tr("Needs attention"))
    if (state.reset_at is not None and state.reset_at > now
            and state.quota_state != "UNANCHORED"
            and (state.quota_state == "ANCHORED" or state.status == "Ready")):
        return make("active_window", tr("Everything is set"),
                    tr("The countdown runs locally. UsageLoop follows your saved schedule after this window ends."),
                    "success", tr("CLOCK RUNNING"), format_countdown(state.reset_at, now),
                    tr("Codex confirmed this reset time. No Codex traffic is needed for the countdown."),
                    _active_window_next_action(settings, state.reset_at, now),
                    tray=format_countdown(state.reset_at, now) + tr(" left"))
    if (state.quota_state == "UNANCHORED" and state.last_verified_at is None
            and state.weekly_used_percent is not None
            and (state.reset_at is None or state.reset_at > now)):
        return make("first_window", tr("Ready when you choose to start"),
                    tr("Starting a first window requires your approval."), "info",
                    tr("FIRST START"), tr("No clock running yet"),
                    tr("The first start uses a guarded Codex request after you approve it."),
                    tr("First window starts only when you ask"), manual=True,
                    tray=tr("Waiting for first window"))
    if state.reset_at is not None:
        try:
            summary = schedule_summary(settings.schedule_mode, boundary_reset_at=state.reset_at,
                now=now, hour=settings.daily_start_hour, minute=settings.daily_start_minute,
                weekly_times=settings.weekly_start_times)
        except (OSError, OverflowError, ValueError):
            return make("needs_attention", tr("Schedule needs attention"), tr("The saved schedule cannot be read safely."),
                        "warning", tr("NEEDS ATTENTION"), tr("Schedule unavailable"), state.detail,
                        tr("Start blocked until the saved schedule is usable."), tray=tr("Needs attention"))
        if summary.phase == "overnight_pause":
            next_action = tr("Overnight pause · first start ") + _time(summary.next_action_at, now)
            return make("overnight_pause", tr("Overnight pause"),
                        tr("Your routine is waiting for its next first start."), "info",
                        tr("OVERNIGHT PAUSE"), tr("Waiting for first start"),
                        tr("The previous window ended. Your saved routine is paused overnight."),
                        next_action, tray=next_action)
        if not summary.due:
            next_action = tr("Waiting for scheduled start ") + _time(summary.next_action_at, now)
            return make("waiting", tr("Waiting for your scheduled start"),
                        tr("The previous window ended. UsageLoop will follow your saved time."),
                        "info", tr("WAITING"), tr("Waiting for saved start"),
                        tr("Last checked quota remains visible below."), next_action,
                        tray=next_action)
        return make("due", tr("Safety checks are due"),
                    tr("UsageLoop is checking whether the next start is safe."),
                    "info", tr("CHECKING"), tr("Next window due"),
                    tr("A start still needs fresh quota and history checks."),
                    tr("Safety checks are due now"), tray=tr("Next window due now"))
    if state.status not in {"Ready", "Waiting", "Detected"}:
        return make("needs_attention", tr("Status unavailable"), tr("Codex status needs a fresh check."),
                    "warning", tr("NEEDS ATTENTION"), tr("Status unavailable"),
                    tr("Sync usage to check Codex again."), tr("Sync usage to check Codex"),
                    tray=tr("Status unavailable"))
    return make("first_run", tr("Waiting for a verified Codex window"),
                tr("Sync usage to read the current state. A first start needs your approval."),
                "info", tr("NOT CHECKED"), tr("No reset clock verified yet"),
                tr("Read-only usage evidence is needed before a start."),
                tr("Sync usage to check the current window"), tray=tr("Waiting for Codex status"))


def _active_window_next_action(settings: AppSettings, reset_at: float, now: float) -> str:
    try:
        summary = schedule_summary(
            settings.schedule_mode, boundary_reset_at=reset_at, now=now,
            hour=settings.daily_start_hour, minute=settings.daily_start_minute,
            weekly_times=settings.weekly_start_times,
        )
    except (OSError, OverflowError, ValueError):
        return tr("Next start waits for this window to reset and a usable schedule")
    target = _time(summary.next_action_at, now)
    if target == tr("when the schedule allows"):
        return tr("Next start waits for this window to reset and a usable schedule")
    if summary.phase == "overnight_pause":
        return tr("After this window: overnight pause · first start ") + target
    if summary.phase == "scheduled_first_start":
        return tr("Next start ") + target
    return tr("Next start around ") + target + tr(", after this window resets")


def _time(timestamp: float | None, now: float) -> str:
    if timestamp is None:
        return tr("when the schedule allows")
    try:
        target = datetime.fromtimestamp(timestamp)
        today = datetime.fromtimestamp(now).date()
    except (OSError, OverflowError, ValueError):
        return tr("when the schedule allows")
    clock = format_datetime(target, "%I:%M %p")
    if target.date() == today:
        return (tr('today at {0}').format(clock))
    if target.date() == today + timedelta(days=1):
        return (tr('tomorrow at {0}').format(clock))
    return format_datetime(target, "%a, %b %d at %I:%M %p")


def _absolute_time(timestamp: float | None) -> str:
    if timestamp is None:
        return tr("the saved time")
    try:
        return format_datetime(datetime.fromtimestamp(timestamp), "%a, %b %d, %Y at %I:%M %p")
    except (OSError, OverflowError, ValueError):
        return tr("the saved time")
