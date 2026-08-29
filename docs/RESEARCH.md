# Protocol Research

Verified 2026-08-29 before implementation. This records the narrow protocol
facts Sentinel depends on so a future Codex update can be checked against
evidence instead of memory.

## Local Environment

- The npm `codex` shim initially resolved to Codex CLI `0.146.0`.
- The native executable bundled under the local OpenAI Codex app updated during
  the session and reports `codex-cli 0.150.0-alpha.12.2`.
- Sentinel prefers the native `codex.exe`; live verification therefore used
  `0.150.0-alpha.12.2`.
- JSON schema generated from both local runtimes contains the stable request,
  response, and notification shapes used here.
- Python 3.12 is not installed. The machine has compatible 3.11, 3.13, and
  3.14 runtimes; implementation verification uses Python 3.13.

## Initialization Contract

The current app-server is newline-delimited JSON over stdin/stdout. Each
connection must send one `initialize` request with `clientInfo`, wait for its
response, then send an `initialized` notification. Requests sent before that
handshake are rejected.

Sentinel opts out of experimental APIs and sends no thread or turn methods.
It deliberately discards the returned `codexHome` path and retains only safe
runtime metadata.

Official source:

- [App-server initialization documentation](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server/README.md#initialization)
- [Initialize protocol schema source](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server-protocol/src/protocol/common.rs)

## Rate-Limit Read Contract

The one account method Sentinel calls is:

```json
{"method":"account/rateLimits/read","id":2,"params":null}
```

The response contains:

- `rateLimits`: backward-compatible single-bucket snapshot;
- `rateLimitsByLimitId`: multi-bucket snapshots keyed by metered limit ID;
- per-window `usedPercent`, nullable `windowDurationMins`, and nullable
  `resetsAt` Unix seconds;
- nullable explicit reached/blocked fields on each bucket.

Sentinel uses `rateLimitsByLimitId` when available and falls back to
`rateLimits`. It identifies a five-hour candidate by duration near 300 minutes,
not by `primary` or `secondary` position.

The official handler validates Codex-managed ChatGPT authentication, creates a
backend client from that auth, and performs the usage read. Authentication and
network communication stay inside Codex; Sentinel receives only the app-server
response.

Official source:

- [Rate-limit app-server documentation](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server/README.md#7-rate-limits-chatgpt)
- [`GetAccountRateLimitsResponse` and window types](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server-protocol/src/protocol/v2/account.rs)
- [Read request handler](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server/src/request_processors/account_processor.rs)
- [Official app-server fixture tests](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server/tests/suite/v2/rate_limits.rs)

## Update Notification

`account/rateLimits/updated` is a sparse rolling update. Missing or null
account metadata must not clear a previous full snapshot. The app-server emits
this notification from token-count events associated with model activity.

A dedicated observation-only Sentinel process creates no turns, so it should
not expect those events. `watch` polls `account/rateLimits/read` and accepts
notifications opportunistically; polling remains authoritative.

Official source:

- [Notification event translation](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server/src/bespoke_event_handling.rs)
- [Sparse notification type](https://github.com/openai/codex/blob/6478a751fde8884b2fdc76486fe23175a8e795d4/codex-rs/app-server-protocol/src/protocol/v2/account.rs)

## Secondary References

- `D1NOOO/codex-usage-monitor` corroborates the child app-server stdio pattern
  and duration-based bucket selection. Its executable discovery informed the
  Windows resolver, but its behavior and assumptions were not treated as
  protocol authority.
- Current `wavever/CCLimitPing` commit `d2d521c` and its v0.9.0 changelog were
  inspected for Phase 2. Its private usage reads and credential handling remain
  rejected. Its MIT-licensed v0.4.2 trigger lesson was retained: headless
  `codex exec` did not reliably anchor the subscription window, while the base
  interactive `codex [PROMPT]` TUI under a PTY did. Sentinel adapts its bounded
  quiet-period and shutdown strategy for Windows ConPTY. Attribution is in
  `THIRD_PARTY_NOTICES.md`.

## Phase 2 Installed Trigger Evidence

- The native executable remains `codex-cli 0.150.0-alpha.12.2` and supports a
  positional initial prompt on the base interactive command.
- Official CLI documentation identifies the base `codex` command as the stable
  terminal UI and `codex exec` as the separate non-interactive mode.
- `codex debug models` reported `gpt-5.4-mini` as visible on this installation,
  described it as small, fast, and cost-efficient, and listed `low` reasoning
  as supported. Sentinel has no fallback model list and does not escalate.
- Windows ConPTY was exercised locally with a benign console command. Its child
  output was captured rather than attached to the calling terminal.

## Stability Warning

Codex subscription window semantics and app-server payloads are evolving
implementation behavior, not a durable public billing API contract. Sentinel
measures reported behavior and returns `UNKNOWN` when evidence no longer fits
the verified shape.
