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

## 2026-08-30 (final polish)

- **Did:** Replaced the raw monospace Diagnostics dump with a readable health
  surface, added a footer status strip so a tall window ends deliberately, and
  swept the remaining spacing and wording.
- **Learned:** Growing cards to fill a tall window made it worse, not better.
  The extra height became hollow card interior, which reads as a bug rather than
  as generous spacing. Honest empty space above a footer beat a stretched card,
  so that attempt was reverted rather than kept.
- **Learned:** PowerShell bound `-H 768` to something else entirely and passed
  a garbage height, which made the capture harness report a window size the app
  never had. Two rounds of chasing a phantom layout bug came from trusting the
  harness output instead of checking the parameters it actually received.
  Unambiguous parameter names fixed it.
- **Verified with:** 273 deterministic tests, and captures of the real packaged
  executable at 1024x768, 1366x768, maximized, and at 125% and 150% scaling.
- **Blocked on:** Nothing. Provider, scheduler, updater, and version behaviour
  untouched this pass.

## 2026-08-30 (consumer clarity)

- **Did:** Changed the mark to `5hr` and shrank it in the header, stopped the
  dashboard claiming "Waiting for reset" for a provider that was never checked,
  replaced a premature "ALL GOOD" with "Setup OK", and rewrote the About page in
  plain language with the evidence moved into Technical details.
- **Learned:** The worst copy bugs were the ones that sounded fine. "Waiting for
  reset" reads perfectly until you notice the app has never seen a reset, and
  "All good" reads perfectly until you notice nothing has been verified. Both
  came from mapping an internal enum straight to a user-facing word without
  asking whether the state actually earned it.
- **Learned:** Raising the assurance strip from 12px to 13px for legibility cost
  enough vertical space to push it off a 768-tall window. Shorter copy paid for
  the larger type, which is a better trade than shrinking the text back.
- **Verified with:** 284 tests, and captures of the real packaged executable at
  1024x768, 1366x768, maximized, and at 125% and 150% scaling.
- **Blocked on:** Nothing. Provider, scheduler, updater, safety, and version
  behaviour untouched.
