# 1.3.9 release candidate review

Updated October 28, 2026 after PR #4 merged. This release candidate remains local;
no branch push, new PR, workflow approval or release is authorized by this step.

## Baseline and history

The branch is `codex/release-candidate-1.3.9`, based on verified remote main at
`8b5ad0a667ad15e11b129a011a53c08727a20621`, the merge of
[PR #4](https://github.com/benthompsondev/usageloop/pull/4).

Main already contains hefengq's original localization commit `ff7bffd` and
Ben's correction `7a5d3d4`, with the original authorship intact. Neither commit
is replayed in this candidate. The old `codex/community-1.3.9` branch remains
at `927a05db56d5267ac6a4b99992a141a0c83d6f63` as a preserved checkpoint.
Only its remaining release work was carried onto the new branch. An unrelated
trailing-blank-line cleanup and an unchanged Settings screenshot refresh were
removed from the release diff.

The latest published release remains 1.3.8. Version 1.3.9 is a local candidate,
with matching project, application and installer metadata.

## Changes against current main

- Both green bars show remaining allowance. Used percentages remain visible.
- Unknown quota has an unchecked/unavailable label and an empty disabled bar.
- A saved reset passing keeps the previous percentage visible and asks for a
  sync. A stale weekly reading cannot retain a green SAFE badge. The existing
  99%-used protection still takes priority, including after reset time.
- The last-sync label uses the quota observation timestamp before the older
  clock-confirmation timestamp.
- Regression tests cover normal, empty, exhausted, unknown, expired, protected
  and uncertain-send states, including both languages for normal percentages.
- CI adds an explicit upgrade from published 1.3.8. Existing scenarios remain.
- Release metadata, quota documentation, dashboard previews and draft release
  notes are prepared. The product contract's dated Linux/model proof wording is
  reconciled with the existing implementation and acceptance record.

The main changes live in `src/sentinel/ui_components.py`,
`src/sentinel/desktop.py`, `src/sentinel/translations.py`,
`tests/test_quota_display.py`, and `.github/workflows/verify.yml`.

The diff leaves core state, scheduling, quota classification, request execution,
reservation/history, protocol, transport and duplicate guards unchanged. The
merged translation engine, date/time formatting, stable widget identifiers,
localization tests and localization guide also match main exactly. Stored quota
still means **used**; subtraction from 100 happens only at the display boundary.

## Validation

- `scripts/verify.ps1` passed on the reconciled branch: **598 tests run, 10
  skipped**, compileall, CLI help and desktop help. Windows, Python 3.13.16,
  PySide6 6.12.0. The skips are Linux/environment-only checks.
- PSScriptAnalyzer reported no errors in the changed install verifier. The
  PowerShell parser also passed. Product tests check the new 1.3.8 matrix entry
  and both download/installer mappings.
- Native Windows Qt captures rendered the real widgets in English and Chinese
  using fake providers and temporary app state. Normal dashboard captures and
  Chinese Settings/history/update surfaces were inspected. Captures are under
  `artifacts/rc-1.3.9`. There was no live Codex traffic.
- The Windows onedir executable was rebuilt from this candidate with
  PyInstaller. The provider-free packaged first-launch, background no-pop and
  second-launch restore checks passed; the executable reports version 1.3.9.
  The archive contains both translation modules. This does not establish a full
  installer upgrade or full packaged Chinese UI acceptance.

The merged baseline's [validate run](https://github.com/benthompsondev/usageloop/actions/runs/40730024021)
passed. Its [verify run](https://github.com/benthompsondev/usageloop/actions/runs/40730023965)
completed all 19 jobs successfully: Windows/Linux tests, both package builds,
and 15 existing Windows installation scenarios. Those results establish the
baseline. The new candidate, including its 1.3.8 upgrade scenario, still needs
its own CI run after approval to publish the branch.

An earlier intermittent native access violation occurred in unchanged Windows
IPC tests, also using the earlier Qt environment. It did not recur in this
candidate's full local test run, and baseline CI passed. There is no established
Qt-version diagnosis and no speculative IPC patch or dependency pin. Retain and
investigate the evidence if the failure recurs in candidate CI.

## Readiness and release gates

The branch is ready to publish for PR review after Ben's approval and the normal
push preflight. The final diff has no unexpected workflow permissions, security,
scheduling or localization changes. That is separate from release acceptance.

Remaining gates before a release:

1. Candidate CI must pass on Windows and Linux, build both packages, and pass
   the installation matrix including the new upgrade from 1.3.8.
2. Check the packaged Chinese UI and language persistence in a disposable
   Windows environment and on a Linux desktop with a CJK font. Check tray or
   no-tray behavior, synthetic settings/history preservation and read-only Sync.
3. Approve version 1.3.9, the final tag/commit, notes and four matching assets.
   Verify checksums, download links and updater metadata before closing Issue #3.

No Inno Setup compiler is available locally. The earlier compiler installation
attempt was blocked by automatic approval review and was not retried here. No
installer acceptance was run against the user's installed software. Local WSL
only lists Docker Desktop, whose engine is unavailable, so there is no local
Linux desktop/package acceptance result. CI provides the next package checks.
No live window start was initiated for testing.

[Issue #2](https://github.com/benthompsondev/usageloop/issues/2) still lacks the
versions and matching start/diagnostic record needed for a diagnosis. The
screenshot shows the no-retry safeguard; the candidate does not claim to fix
its cause. Keep it open and use the prepared question in the
[maintainer checklist](maintainer-checklist.md).

Draft notes: [1.3.9 release notes](1.3.9-release-notes.md).
