# Learning Log

## 2026-08-29

- **Did:** Built the Phase 1 observer test-first, added local setup, and verified it through the real app-server.
- **Learned:** The native app Codex can be newer than the npm shim; live selection must report the executable it actually uses.
- **Verified with:** 31 deterministic tests, compile/CLI checks, live doctor, and a four-sample session that found a fixed reset timestamp.
- **Blocked on:** Nothing in the Phase 1 observer scope.
- **Next:** Treat this commit as the observation foundation when the later window-chaining product is designed.

## 2026-08-30

- **Did:** Rebranded the Windows app to UsageLoop, replaced the single-resolution
  icon with a real multi-size mark, rewrote the dashboard for a non-technical
  user, and fixed three real defects found while verifying the product.
- **Learned:** `dataclasses.asdict` silently drops properties. The build script
  read product metadata that way, so a derived folder name came back empty and
  the Claude status helper was built outside the app folder and left out of the
  installer entirely. A build that "succeeds" is not a build that shipped the
  right files.
- **Learned:** A dataclass does not enforce its annotations, so a corrupt
  `app-state.json` loaded a string where a timestamp belonged and raised
  TypeError inside the one-second clock tick.
- **Learned:** A 404 from the GitHub releases API means "nothing published yet",
  not "no network". Reporting it as a connection failure would have greeted every
  early beta user with a scary and untrue message.
- **Learned:** Headless icon rendering cannot rely on a font being present, and a
  glyph is unreadable below about 48px anyway. Dropping "5h" from the icon made
  it both safer to build and clearer at tray size.
- **Verified with:** 225 deterministic tests, compile check, real provider
  detection for Codex and Claude, a real GitHub release lookup, packaged EXE
  launch, installer build with matching SHA-256, and rendered screenshots
  including 1366x768.
- **Blocked on:** Nothing. Claude window anchoring stays unproven and the UI now
  says so rather than implying it works.
- **Next:** Publish the first release so the updater path can be tested from an
  older installed build.
