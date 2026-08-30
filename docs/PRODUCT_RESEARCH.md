# Window Sentinel: Product Research and Design Pass

> Historical record from 2026-08-29, kept as written. The product was
> renamed to UsageLoop on 2026-08-30; the GitHub slug did not change.

Read-only research and design. No production code changed, no Codex request sent,
no quota consumed. The primary Codex account remained at 100% five-hour
availability throughout.

Date: 2026-08-29
Repo state reviewed: `ae8ecb4` (Phase 2 chaining) on top of `7fd26a1` (Phase 1 observer)

## Why this pass exists

Phase 2 works, and its live behaviour on a pristine account produced exactly the
result the safety rules require:

```text
ROLLOVER_BOUNDARY_UNKNOWN
No recent anchored reset proves that a genuine rollover occurred.
Request sent: no
```

That is correct engineering and a broken product. A first-time user installing
Sentinel on a fresh window cannot get value from it, because the tool demands
yesterday's history that it never had a chance to collect. This document decides
how to fix that safely, reviews the Codex implementation for material defects,
determines whether the same product is buildable for Claude Code, and picks the
shipping architecture.

---

## 1. Comparison matrix

| Project | License | Providers | Observation mechanism | Trigger mechanism | Windows story | What it teaches us |
| --- | --- | --- | --- | --- | --- | --- |
| **Codex Window Sentinel** (this repo) | MIT | Codex | Local `codex app-server`, `account/rateLimits/read`, no credentials read | Interactive `codex` TUI under ConPTY, one attempt per proven boundary | Windows-first, CLI only, venv install | Baseline |
| **wavever/CCLimitPing** | MIT | Claude, Codex, Spark | Undocumented zero-quota HTTP: `api.anthropic.com/api/oauth/usage`, `chatgpt.com/backend-api/wham/usage`, using tokens read from Keychain / `.credentials.json` / `auth.json` | `claude --model haiku .`, `codex -c model_reasoning_effort=low -m gpt-5.4-mini ok`, TTY-backed | "Limited": no Keychain, no notifications | Timing strategy (already adapted and attributed), `weekly_threshold = 0.99`, `align_start` bootstrap, and the best idea in the field: CLI hooks that detect an in-flight user turn and skip the ping |
| **tesuheee/headroom-ai-usage-monitor** | MIT | Claude, Codex | Reads `%USERPROFILE%\.claude\.credentials.json` and `%USERPROFILE%\.codex\auth.json`, calls provider usage APIs directly; also offers built-in browser PKCE OAuth | None, monitor only | Windows 10/11 only, .NET 4.8, standalone exe, floating widget | The closest match to our target UX: per-provider cards, independent auth state, remaining/used toggle, threshold colouring, no terminal |
| **zhuchenxi113/ai-limit** | Apache-2.0 (bundles LGPL `browser-cookie3`) | Claude, Codex | Layered fallback: browser cookies to `claude.ai/api/organizations/{orgId}/usage` and `chatgpt.com/backend-api/codex/usage`, then `codex app-server`, then local session JSONL | None | macOS menu bar and Windows 11 tray, PyInstaller + Inno Setup, unsigned exe, no admin | The Windows packaging recipe we should copy, and one claim we must keep testing (below) |
| **tddworks/ClaudeBar** | MIT | Claude, Kimi, Grok, OpenCode | Provider CLI output, provider APIs, local SQLite, browser cookies | None | macOS 15+ / Swift only | Architecture confirmation: one `QuotaMonitor` as single source of truth, views consume domain objects, providers behind adapters |

### The one claim worth arguing with

`ai-limit` documents its `codex app-server` account rate-limit path as one that
**triggers the 5-hour window**. Sentinel's entire Phase 1 design assumes the
opposite: that `account/rateLimits/read` is free.

Our own live evidence contradicts theirs. The primary account sat at 100%
availability with no Codex work since the previous reset, Sentinel sampled it
repeatedly through the app-server, and the account still classifies as
`UNANCHORED` with 100% remaining. If the read anchored a window, that account
would read `ANCHORED`. It does not.

Measured evidence beats a third-party README, so Phase 1's assumption stands.
But this is undocumented, evolving behaviour and the disagreement is not
academic: if a Codex update ever makes the read billable, Sentinel silently
becomes the thing it was built to avoid. Treat "the rate-limit read is free" as
a standing regression test, not a settled fact. See the decisive tests below.

### Lessons we should actually adopt

1. **In-flight turn detection (from CCLimitPing).** If the user is mid-turn or
   about to prompt anyway, triggering is pure waste. CCLimitPing installs CLI
   hooks so `watch` defers to the user's own next request. We can do the same
   with far less intrusion: on Claude via a `UserPromptSubmit` or `SessionStart`
   hook, on Codex via its hook surface. This is the single highest-value
   behaviour we do not currently have, and it reduces quota spend rather than
   increasing it.
2. **Weekly protection as a first-class gate (convergent).** CCLimitPing's
   `weekly_threshold = 0.99` matches our `weekly_protection_percent = 99`
   independently. Two implementations landing on the same guard is a good sign
   the guard is right.
3. **Never inherit credential parsing (from all three monitors).** Every project
   that reads usage without a CLI does it by reading OAuth tokens off disk, out
   of Keychain, or out of browser cookies. All three are the same trade: better
   observability for a much worse security boundary. Our refusal to do this is
   the actual product differentiator, not a limitation.
4. **Packaging is a solved problem on Windows (from ai-limit).** PyInstaller
   plus Inno Setup, per-user install, no admin rights, separate CLI executable
   alongside the GUI. That is a proven path for a Python engine like ours.
5. **Single source of truth in front of provider adapters (from ClaudeBar).**
   Their `QuotaMonitor` layering is the same shape we need for a two-provider
   app, and it is the shape that survived contact with four providers.

---

## 2. Material Codex review findings

Findings verified against the code at `ae8ecb4`. Ordered by impact. Nothing here
is speculative; where I am uncertain I have said so and moved it to the tests
section instead.

### F1. The trigger cannot launch a `.cmd` shim, and burns the boundary trying

`find_codex_executable` accepts `codex.exe` **or** `codex.cmd` on PATH
(`transport.py:44`). For the app-server path, `_command()` correctly wraps a
`.cmd` or `.bat` in `cmd.exe /d /c` (`transport.py:184-187`).

`InteractiveCodexTrigger.command()` has no such wrapping (`trigger.py:65-80`).
It passes the executable straight through to `CreateProcessW` as
`lpApplicationName` (`_conpty.py`, the `application = str(Path(command[0]))`
line). `CreateProcessW` cannot execute a batch shim: it is not a PE image, so
the call fails with `ERROR_BAD_EXE_FORMAT`.

The damage is not the failure. It is the ordering. `chain.py:151` writes
`record_trigger_attempt` **before** `self.trigger.run()`. So a user whose Codex
resolves to the npm `codex.cmd` shim gets:

- reservation written and permanently consumed,
- process never started, zero quota spent,
- `TRIGGER_FAILED`,
- and `ATTEMPT_ALREADY_RECORDED` forever after for that rollover.

The most likely install shape for a non-technical user is exactly the one this
breaks. This is the highest-severity finding in the review.

**Fix direction:** reuse `transport._command()` for the trigger, or resolve to a
native `codex.exe` before reserving, and preflight the executable shape as part
of eligibility rather than discovering it after the reservation is spent.

### F2. `chain`'s own preflight can never reach high confidence, and sits on the classifier's cliff edge

`cli.py:233-244` collects four observations with `time.sleep(5.0)` between them:
roughly 15 seconds of span. The classifier requires `elapsed >= 15` or it
returns `UNKNOWN` (`classifier.py:59-60`), and `_confidence` awards `high` only
at `sample_count >= 4 and elapsed >= 30` (`classifier.py:140-141`).

So `chain` is structurally pinned to `medium` confidence, which is exactly what
the live run reported, and it sits directly on the 15-second floor. A slightly
fast loop, clock granularity, or a scheduling hiccup flips a correct `UNANCHORED`
into `UNKNOWN` and a correct decision into `NOT_ELIGIBLE`. Meanwhile `sample`
uses 4 reads at 10 seconds and comfortably earns `high`.

The command that is allowed to spend quota gathers weaker evidence than the
command that cannot. That is backwards.

**Fix direction:** `chain` preflight should be the strictest observation in the
tool, not the loosest. See the bootstrap gates in section 3.

### F3. `succeeded` and the category `turn_completed` overclaim what ConPTY can observe

`trigger.py:99` returns `TriggerRunResult(True, "turn_completed")`. What the
ConPTY runner actually observed is one of three things (`_conpty.py` main loop):

- the process exited after `min_runtime_seconds`,
- output went quiet for `quiet_seconds` after `min_runtime_seconds`, or
- `max_runtime_seconds` (45s) elapsed and we broke out.

In the last two cases the process is still alive, gets a `\x03`, possibly a
second `\x03`, and then `TerminateProcess` if it will not leave. A TUI killed
mid-stream is precisely the case where quota may have been consumed with no
completed turn. All three paths return the same `succeeded=True` and the same
literal string `turn_completed`.

This repo's stated standard is that a false `UNKNOWN` beats a false confident
answer. The trigger layer does not meet that standard. It should report what it
saw (`process_exited`, `output_quiesced`, `runtime_capped`, `terminated`) and let
the verification step, which actually can prove anchoring, own the verdict.

### F4. A failed-looking trigger skips verification entirely

`chain.py:157-166`: if `trigger_result.succeeded` is false, `chain` records the
outcome and returns `TRIGGER_FAILED` without collecting verification
observations.

This contradicts the repo's own reasoning for the one-attempt rule, stated in
`PROJECT_SPEC.md`: an observer cannot prove that an unverified request consumed
zero quota. If that is true, then a trigger that looked like it failed might
still have anchored the window, and the cheap read that would settle it is
skipped. The user is told it failed, the boundary is spent, and `chain` will
never look again.

**Fix direction:** always run the verification pass once the process has been
started, regardless of how the process looked. The observation is free. Report
`ANCHOR_VERIFIED` if the window anchored even when the process teardown was
ugly.

### F5. The rollover-history gate has roughly one hour of usable slack

`latest_anchored_reset_before` (`history.py:130-160`) requires an `ANCHORED`
observation row whose `observed_at` is within `max_age_seconds = 21600` (6h) and
whose `resets_at` is already past.

The five-hour window is 5h long. If a user samples at the very start of an
anchored window, that row is already 5h old at the moment of rollover, leaving
under an hour before it ages out of the gate. Sample mid-window and it is
better; sample early and come back 70 minutes after rollover and you get
`ROLLOVER_BOUNDARY_UNKNOWN` despite having done everything right.

Not a bug, but an undocumented usability cliff that compounds the first-run
problem and should be stated in the README as a constraint, or widened once the
bootstrap path exists.

### F6. Codex launches with the user's full global config, so "minimal request" is not guaranteed

The trigger uses a bare workspace with `-s read-only -a never`
(`trigger.py:65-80`), which correctly bounds the sandbox. It does not bound
*configuration*. Codex still loads `~/.codex/config.toml`, which on a real
developer machine may start MCP servers and inject instructions, all of which
enter the context of the very turn we are advertising as a two-character
request.

The privacy boundary holds (we log nothing), but the quota claim weakens. If the
product promise is the smallest possible billable request, the trigger should
neutralise MCP servers and project instruction discovery explicitly rather than
relying on an empty directory.

### F7. Orphan reservations when verification throws

If `collect_verification()` raises (app-server dies right after the trigger, a
plausible outcome given we just terminated a child process), the exception
escapes `ChainCoordinator.run` and is caught by `cli.main`. The
`trigger_attempt` row is on disk with no matching `trigger_result`, the user
sees a generic error, and the boundary is spent with no record of what happened.

**Fix direction:** write the outcome in a `finally`, with an explicit
`verification_unavailable` category.

### F8. The safe log grows without bound and is fully re-parsed on every gate check

`SafeHistory._read_rows` and `load_recent` read the entire JSONL into memory
(`history.py:162-209`). `watch` at the default 30-second interval writes about
2,880 observation rows per day with no rotation, retention, or size cap. Every
`chain` invocation re-parses the whole file twice.

Harmless today, wrong for a shipped consumer app that is expected to run
continuously. Needs rotation and a bounded tail read before packaging.

### Things I checked and did not find a problem with

Worth recording so a later pass does not re-litigate them:

- **Duplicate reservation across restart** is correctly handled. The reservation
  is written to durable JSONL before the process starts and counted by boundary,
  so a crash mid-trigger cannot produce a second request. That is the right
  trade and the code makes it.
- **Weekly protection** fails safe. `_select_weekly` (`chain.py:201-210`) returns
  `None` on any ambiguity, and `None` maps to `WEEKLY_UNAVAILABLE`, which blocks.
- **Already-anchored protection** does not depend on the history gate at all.
  `chain.py:71-79` returns `ALREADY_ANCHORED` from the classifier alone. This
  matters for section 3: relaxing the *history* rule does not weaken the
  already-anchored guard.
- **Safe logging** holds. Evidence keys are allowlisted, categories and versions
  are regex-gated, the trigger prompt is recorded only as a character count, and
  `test_trigger.py` asserts the prompt text does not appear in the description
  repr.
- **ConPTY handle hygiene** is careful: inheritance is cleared on the parent-side
  handles, the child-side pipe ends are closed after `CreatePseudoConsole`, and
  the `finally` block covers every handle and the attribute list.

---

## 3. Recommended first-run bootstrap behaviour

### What the history gate is actually protecting

Before changing it, be precise about what it buys, because it buys less than it
appears to:

- It does **not** prevent triggering an already-anchored window. The classifier
  does that, independently.
- It does **not** prevent duplicate spend by itself. The per-boundary
  reservation does that, and it needs a boundary key to exist.
- It **does** guard against a false `UNANCHORED` classification.
- It **does** guard against Sentinel starting a window the user never asked for.

Only the last two need a replacement. Both have better answers than "wait until
tomorrow."

### Evaluating the four options

**D. Require historical rollover evidence (status quo).** Correct and unusable.
A user whose first install lands on an idle account is told to come back after a
rollover they cannot observe without the tool. Reject.

**B. Automatic bootstrap when evidence is strong enough.** The evidence argument
is sound: a reset timestamp that tracks wall time for a minute while staying
pinned one full window away is stronger proof of "no active window" than a
historical timestamp is. But as a *first* action on a fresh install it spends
the user's quota before the tool has demonstrated anything, in a state the user
cannot inspect, and possibly moments before they were going to start work
anyway, which makes it pure waste. Right steady state, wrong first move.

**C. Calibration or setup flow.** This is option A wearing a wizard. Multi-step
setup is the thing the target UX explicitly rules out.

**A. Explicit first-run action after strong UNANCHORED evidence.** One decision,
in the user's own words, at the exact moment it is meaningful. It doubles as the
tool's first proof of value: the user taps once and watches Sentinel verify that
a real window started.

### Recommendation: A first, then B

**One explicit yes per account buys autonomy afterwards.**

- The **first** trigger on a given install is always user-confirmed.
- After one *verified* bootstrap, real anchored history exists, the original
  safety rule holds on its own terms, and the tool switches to automatic
  chaining. No second confirmation is needed or asked for.

This maps cleanly onto the target UX. Flipping "Keep my 5-hour windows ready" to
ON **is** the consent. If no window is currently running at that moment, the app
surfaces one line: "No 5-hour window is running right now. Start one?" with a
single button. That is the whole flow. No terminal, no config file, no
scheduler, no calibration wizard.

### Bootstrap eligibility gates

A bootstrap trigger requires **all** of:

1. **Strong UNANCHORED evidence, stricter than the current chain preflight.**
   At least 6 observations spanning at least 60 seconds (fixing F2), reset slope
   within 0.1 of 1.0, and the distance to reset pinned within 5 seconds of the
   full declared window duration across every sample. This is deliberately
   stricter than a normal post-rollover chain, because there is no corroborating
   history.
2. **Corroborating usage evidence.** The five-hour window reads `usedPercent`
   of 0. Note the caveat: `openai/codex#32607` documents a first reading of 79%
   with no prior model activity, so a **nonzero** reading is a reason to abstain
   rather than proof of anything. Zero is corroboration; nonzero is a stop.
3. **Weekly window present, below the protection threshold, not blocked.**
   Unchanged from today.
4. **No bootstrap attempt recorded within one full window duration.**
5. **Recorded user consent for this install.**
6. **Not already anchored.** Unchanged, and independent of history.

### Solving the idempotency problem without a boundary key

The reservation is keyed on `boundary_reset_at`, which does not exist on first
run. The fix is not to invent a fake boundary. It is to key the bootstrap
reservation on the *state* instead:

- Write `trigger_attempt` with `boundary_reset_at: null` and `bootstrap: true`,
  plus the observation time.
- The duplicate gate becomes: no bootstrap attempt within the last full window
  duration.

That cooldown is exactly self-healing. A successful bootstrap anchors a window
that lasts one full duration, so during the cooldown the classifier reports
`ANCHORED` and no trigger is even considered. If the bootstrap silently failed,
the cooldown expires precisely when a second attempt becomes legitimate.

### How this prevents each named failure mode

| Failure mode | Prevention |
| --- | --- |
| Duplicate triggers | Reservation written before the process starts, durable across restart; bootstrap keyed by cooldown rather than boundary |
| Triggering an already-anchored window | Classifier `ALREADY_ANCHORED` check, which never depended on history |
| Repeated quota consumption when verification fails | One full-window cooldown per failed bootstrap, plus a hard cap of two lifetime bootstrap attempts before the app requires an explicit re-confirmation |
| Weekly-limit waste | Existing weekly gate, unchanged, evaluated before the bootstrap path |
| A false `UNANCHORED` costing the user a window | Stricter evidence bar than a normal chain, plus `usedPercent == 0` corroboration, plus explicit consent on the first one |

The user-facing consequence of a worst-case bootstrap error is one minimal
haiku-class or mini-class request. That is a proportionate risk to take once,
with consent, in exchange for the tool working at all on day one.

---

## 4. Claude Code provider feasibility

### The finding that shapes everything else

**Claude has no zero-cost, credential-free, out-of-session usage read.** There is
no equivalent to Codex's `account/rateLimits/read`. Do not assume Claude can use
Codex's plumbing; it cannot use any of it.

What actually exists, verified against current official docs:

| Surface | Machine-readable? | Cost | Usable as our observer? |
| --- | --- | --- | --- |
| `statusLine` stdin JSON, `rate_limits.five_hour.{used_percentage, resets_at}` | Yes, `resets_at` in Unix epoch seconds | Free, the statusline runs anyway | **Yes**, with hard constraints |
| `/usage` | No, interactive TUI rendering only | Makes a usage request | No |
| Hooks (`SessionStart`, `Stop`, all others) | Yes, but no quota fields exist in any hook payload | Free | No for observation, **yes** for in-flight turn detection |
| `claude -p --output-format json` | Yes | Consumes subscription quota | Trigger candidate, not an observer |
| `api.anthropic.com/api/oauth/usage` with a token read off disk | Yes | Free | **Rejected**, see below |
| Browser cookies to `claude.ai/api/organizations/{orgId}/usage` | Yes | Free | **Rejected**, see below |

The official statusLine documentation states the constraints precisely:
`rate_limits` appears only for Claude.ai Pro and Max subscribers, and only after
the first API response in the session; each window (`five_hour`, `seven_day`,
`spend_limit`) may be independently absent, and Claude Code drops a window once
its `resets_at` time passes.

### Two consequences, one bad and one very good

**Bad:** the observation is only available from inside a live Claude Code session
that has already made a request. You cannot poll it while nothing is running.
Codex's model of an independent observer process does not port.

**Good:** the detection logic gets *simpler*, not harder. Claude does not report
a sliding future reset the way Codex does. It **drops the window entirely** once
`resets_at` passes. So:

- `five_hour` present with a future `resets_at` = anchored, with the exact
  boundary handed to us.
- `five_hour` absent, in a Pro/Max session that has had an API response = no
  active window.

No sliding-slope classifier, no jitter tolerances, no `UNANCHORED` inference.
The Codex classifier should not be reused or generalised for Claude; it solves a
problem Claude does not have.

### Recommended mechanism

**Observation: a statusLine passthrough recorder.**

With consent, Sentinel writes a small recorder script and registers it as the
`statusLine` command in `~/.claude/settings.json`. On each invocation it:

1. appends `five_hour` and `seven_day` `used_percentage` and `resets_at` to
   Sentinel's own JSONL, and
2. delegates to whatever `statusLine` command the user already had, passing
   stdin through unchanged, so their display is untouched.

Cost is zero, because the statusline already runs. Every normal Claude session
becomes a free anchored-history contributor. This is the Claude equivalent of
Phase 1, and it largely dissolves the bootstrap history problem for Claude,
because the tool accumulates real anchored boundaries from ordinary work.

**Trigger: test `-p` first, fall back to ConPTY.**

`claude -p --model haiku --no-session-persistence "ok"` uses the same OAuth
authentication as interactive mode and counts against the subscription, not an
API key. If it anchors the window, it is dramatically simpler than driving a
TUI: no ConPTY, no quiet-period heuristics, no terminate path, and none of
findings F1 or F3 apply.

CCLimitPing chose `claude --model haiku .` (interactive, TTY-backed) for Claude,
mirroring the Codex case where `codex exec` was found not to anchor. Whether
that reflects a measured Claude finding or just consistency with the Codex path
is not documented. Assume nothing: this is decisive test T4.

**Verification: the elegant option, if `-p` does not carry it.**

Verification is the genuinely hard part, because there is no out-of-session read
to confirm with. Two routes, in order:

1. If `claude -p --output-format json` returns rate-limit data in its result
   envelope, verification is free and immediate. Test T5.
2. Otherwise: run the interactive trigger under ConPTY with `--settings`
   pointing at a temporary settings file whose `statusLine` is Sentinel's
   recorder. The trigger session's own statusline invocation writes the
   post-request `five_hour.resets_at` to disk. **The trigger and the verification
   become the same process.** That is a genuinely clean design, and it is the
   fallback to build if T5 comes back negative.

**Weekly protection** carries over directly from `rate_limits.seven_day` in the
same payload, so the existing gate needs no redesign for Claude.

### Explicitly rejected for Claude

- **Reading `~/.claude/.credentials.json` or macOS Keychain and calling
  `api.anthropic.com/api/oauth/usage`** (CCLimitPing, headroom). It works, and it
  puts a long-lived OAuth token through our process against an undocumented
  endpoint. This is the exact boundary Sentinel exists to hold.
- **Browser cookie extraction** (ai-limit). LGPL dependency, Cloudflare
  challenges, Firefox-only on Windows, and it authenticates as the user's
  browser. No.
- **`claude setup-token` long-lived tokens.** Solves an authentication problem we
  do not have and creates a credential we would then own.

---

## 5. Universal app architecture verdict

**Verdict: option 1, one Windows application with shared orchestration and UI
plus Codex and Claude provider adapters.** This is technically honest, with one
condition.

The condition: the shared layer must stop at *policy*. It must not pretend the
classifier is shared, because it is not and cannot be.

| Layer | Shared | Why |
| --- | --- | --- |
| UI, tray, notifications, scheduling, packaging, install | Yes | Identical product surface |
| Policy engine: eligibility, bootstrap consent, reservation, cooldown, weekly gate, verification contract | Yes | The rules are provider-independent and are the actual product |
| State store and safe log | Yes | One JSONL schema with a `provider` field |
| Observation transport | **No** | Codex: JSON-RPC over a child `app-server`. Claude: a statusLine recorder writing from inside the user's own sessions. Nothing in common |
| Window-state determination | **No** | Codex: time-series slope classification. Claude: presence or absence of `five_hour`. Forcing these together would make the Claude path worse |
| Trigger transport | **No** | Codex: interactive TUI under ConPTY. Claude: `-p`, or ConPTY with an injected statusLine |

The adapter interface that makes this honest is deliberately narrow:

```text
observe()          -> WindowState { state, boundary, used_percent, weekly, confidence, evidence }
describe_trigger() -> TriggerDescription
trigger()          -> TriggerRunResult
verify()           -> WindowState
```

The orchestrator never sees a reset slope, a JSON-RPC id, or a statusline
payload. It sees `WindowState` and applies the same rules to both providers.
That is exactly ClaudeBar's `QuotaMonitor` layering, which survived four
providers, and it is the strongest argument that one app is the right call.

Option 2 (separate helper processes) is not needed. The Claude observer does run
outside the app, but it is a small script Sentinel writes and registers, not a
service to supervise. Option 3 (separate applications) would duplicate the
policy engine, which is the one part that genuinely is shared, and would double
the surface where a safety rule could drift between the two.

### Target user experience

```text
Install Window Sentinel
  Codex detected           OK
  Claude Code detected     OK
  Keep my 5-hour windows ready   [ON]

  Codex        no window running    [Start one now]
  Claude Code  4h 12m remaining
```

Everything technical goes under Advanced: raw classification state, evidence, the
safe log path, the trigger plan, the dry-run inspector, and the existing CLI,
which stays and remains the diagnostic tool.

---

## 6. Windows UI and packaging recommendation

Keep the Python engine. Do not rewrite it in .NET or Go to match the field.

- **Engine:** the existing `sentinel` package, extended with the adapter
  interface. Standard library only, as today.
- **UI:** a tray application, single window, per-provider cards, matching the
  headroom shape (compact cards, reset countdown, threshold colouring,
  independent per-provider state) without its credential model.
- **Build and install:** PyInstaller plus Inno Setup, per-user, no admin rights,
  with a separate CLI executable shipped alongside the GUI. This is exactly
  ai-limit's proven Windows path, and it matches the existing "no admin, no
  global PATH" constraint in `AGENTS.md`. Expect an unsigned-binary SmartScreen
  warning until code signing is worth paying for, and document it rather than
  hiding it.
- **Waking at the right time:** a tray process for the UI, plus a per-user
  Windows Task Scheduler logon task for resilience, so a closed tray does not
  silently stop chaining.

**Two of these need Ben's explicit approval before implementation**, because the
current `AGENTS.md` forbids both outright: a GUI, and any auto-start or
scheduled-task behaviour. Flagging, not assuming.

---

## 7. Reusable open-source pieces and licensing obligations

| Project | License | Obligation if we reuse | Reuse | Do NOT inherit |
| --- | --- | --- | --- | --- |
| wavever/CCLimitPing | MIT | Copyright notice and license text retained. Already satisfied in `THIRD_PARTY_NOTICES.md` for the timing strategy; extend the note if we adopt hook-based turn detection | Interactive-TTY trigger rationale (adopted), quiet-period shutdown (adopted), weekly threshold, `align_start` framing, **hook-based in-flight turn detection** | Credential reads from Keychain / `.credentials.json` / `auth.json`; `api.anthropic.com/api/oauth/usage`; `chatgpt.com/backend-api/wham/usage`; auto-installing hooks into the user's config without consent |
| tesuheee/headroom-ai-usage-monitor | MIT | Copyright notice and license text if any code is used. Pure UX inspiration needs no notice, but credit it anyway | Per-provider card layout, remaining/used toggle, threshold colouring, independent per-provider auth state, single-exe no-admin delivery | Reading `.credentials.json` and `auth.json`; the embedded browser PKCE OAuth flow; any direct provider usage-API call |
| zhuchenxi113/ai-limit | Apache-2.0 (`browser-cookie3` is LGPL) | Apache-2.0 requires retaining the license, the NOTICE file if present, attribution, and **stating any changes made**. Vendoring code means adding a NOTICE entry. Copying only the packaging *approach* carries no obligation | PyInstaller + Inno Setup + per-user unsigned install recipe; the separate GUI/CLI executable split; its layered-fallback structure as a cautionary example | `browser-cookie3` entirely (LGPL relinking obligations plus browser cookie extraction); `chatgpt.com/backend-api/codex/usage`; `claude.ai/api/organizations/{orgId}/usage`; the Firefox-only Windows path; and its claim that the app-server read triggers the window, which our evidence contradicts but which we should keep testing |
| tddworks/ClaudeBar | MIT | Copyright notice and license text if any code is used. Architecture inspiration needs none | The single-source-of-truth monitor plus provider adapters layering; views consuming domain objects with no intermediate view models | Everything else. macOS 15 and Swift 6.2, not portable. Also its Full Disk Access cookie requirement |

**General rule for this project:** every one of these tools reaches its data by
holding a credential we have decided not to hold. Reuse their *shapes*, their
*timing*, and their *packaging*. Never reuse their *access paths*.

---

## 8. Decisive tests still required

None of these have been run. Several must be run before any of the above becomes
implementation.

### Codex

- **T1 (regression, highest priority).** Confirm `account/rateLimits/read` still
  consumes no quota, directly contradicting ai-limit's claim. Method: on a
  pristine 100% account, take a full `sample` run and confirm the window remains
  `UNANCHORED` at 100% afterwards. This must become a standing check after every
  Codex update, not a one-off. The current pristine account is the ideal subject
  for this test, and it is the reason not to spend that state casually.
- **T2.** Reproduce F1 by resolving the trigger to a `codex.cmd` shim and
  confirming `CreateProcessW` fails with `ERROR_BAD_EXE_FORMAT`. This can be
  tested with a harmless dummy `.cmd` and no Codex involvement at all, so it
  costs nothing.
- **T3.** Determine whether `CREATE_NO_WINDOW` combined with the pseudoconsole
  attribute is fully supported or silently ignored on Windows 11. I could not
  establish this confidently from documentation, and it affects whether a stray
  console flashes on a consumer machine. Test with a dummy console app, no quota.

### Claude

- **T4.** Does `claude -p --model haiku --no-session-persistence "ok"` anchor the
  five-hour window, or does it behave like `codex exec` and consume without
  anchoring? This is the single most important Claude question and it decides the
  entire trigger design. Costs one minimal request, so run it during an already
  anchored window where the marginal cost is near zero.
- **T5.** Does `claude -p --output-format json` include rate-limit data in its
  result envelope? If yes, verification is trivial; if no, build the injected
  statusLine verification path. Can be answered by the same run as T4.
- **T6.** Confirm the statusLine recorder actually receives `rate_limits.five_hour`
  on Ben's plan and Claude Code version. Issue `anthropics/claude-code#40094`
  reports the field going missing on Max 20x, so this cannot be assumed from
  documentation. Free to test: register a recorder, run one normal session, read
  the log.
- **T7.** Confirm the drop-on-expiry behaviour: that `five_hour` genuinely
  disappears rather than sliding, in a live session after a window expires. This
  is the entire basis of the simplified Claude detection rule. Free.
- **T8.** Confirm the statusLine passthrough does not break or visibly delay an
  existing user statusline. Free.

### Cross-provider

- **T9.** Confirm that a bootstrap trigger on a genuinely idle account produces a
  verified `ANCHORED` result, once. This is the one test that must spend quota
  deliberately, and it is exactly what the pristine account is being preserved
  for. Run it only after F1 is fixed, otherwise the run proves nothing about the
  design and burns the boundary on a shim bug.

---

## Summary of what changes

1. Fix F1 before any live bootstrap test, or the test measures the wrong thing.
2. Fix F4 and F3 so the tool stops throwing away free evidence and stops claiming
   more than it observed.
3. Raise `chain`'s preflight above the classifier's cliff edge (F2).
4. Add the consented bootstrap path with a cooldown-keyed reservation.
5. Build the Claude adapter around the statusLine recorder, not around anything
   borrowed from the Codex transport.
6. One app, shared policy, adapter-isolated transports and classifiers.
7. Get Ben's decision on the two things `AGENTS.md` currently forbids: a GUI, and
   any scheduled or auto-start behaviour.

---

## Sources

Official documentation and runtime evidence:

- Claude Code status line reference, including the `rate_limits` schema and its
  presence rules: https://code.claude.com/docs/en/statusline
- Claude Code hooks reference (no quota fields in any hook payload):
  https://code.claude.com/docs/en/hooks
- Claude Code CLI reference (no usage/quota command exists):
  https://code.claude.com/docs/en/cli-reference
- Claude Code cost management and `/usage` behaviour:
  https://code.claude.com/docs/en/costs
- `anthropics/claude-code#40094`, `rate_limits` missing from statusLine JSON:
  https://github.com/anthropics/claude-code/issues/40094
- `openai/codex#32607`, first rate-limit reading already at 79% with no prior
  model activity: https://github.com/openai/codex/issues/32607

Projects reviewed:

- https://github.com/wavever/CCLimitPing
- https://github.com/tesuheee/headroom-ai-usage-monitor
- https://github.com/zhuchenxi113/ai-limit
- https://github.com/tddworks/ClaudeBar
