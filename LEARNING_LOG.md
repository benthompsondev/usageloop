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

## 2026-08-30 (later)

- **Did:** Fixed the header clipping Ben found on the installed build, switched
  the brand mark to the loop-plus-5h logo with size-adaptive detail, and did a
  visual polish pass on the cards and page container.
- **Learned:** `QSizePolicy.Ignored` is a *growing* policy, not a shrinking one.
  It tells Qt to disregard the size hint and hand the widget as much room as it
  can take. Using it to make the header tagline "flexible" let the brand block
  expand with the window and squeeze the navigation and trust chip against the
  right edge, where a maximized Windows window hides them under its invisible
  resize border. The fix was a label that elides itself and reports a zero
  minimum width, so it can never drive the layout.
- **Learned:** Offscreen Qt and real Windows Qt do not share font metrics, so a
  layout that passes headless can still clip on the desktop. The bug only showed
  up on the packaged executable. Layout checks now run on the real platform and
  assert containment directly instead of checking a minimum width.
- **Learned:** A guessed constant in layout code hides in plain sight. The
  fallback that hides the chip used a hardcoded brand floor of 52px, which was
  right at the default font and wrong at 1.6x, so the chip stayed visible with
  nowhere to go. Measuring the real minimum fixed it, and a larger-font test
  now pins it.
- **Learned:** Drawing the "5h" as explicit path geometry rather than typeset
  text keeps the packaged icon identical regardless of which fonts the build
  machine has. An earlier attempt rendered tofu boxes headless.
- **Verified with:** 241 deterministic tests, compile check, packaged build,
  and screen captures of the real executable maximized, restored, and at 125%
  and 150% DPI.
- **Blocked on:** Nothing. Provider, updater, and scheduler code untouched.
- **Next:** Publish the first release so the updater can be tested from an
  older installed build.
