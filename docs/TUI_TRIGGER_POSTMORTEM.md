# Why the ConPTY TUI trigger was retired

Historical record. The code described here no longer exists; this file explains
why, so the approach is not reinvented.

Phase 2 originally launched the interactive Codex TUI inside a Windows pseudo
console and drove it with a small state machine that read rendered screens. It
never reached a submitted turn on a real machine. Four independent blockers were
confirmed by capturing live ConPTY output and replaying it through the shipped
controller.

## 1. Cursor-forward escapes are used instead of spaces

Codex separates words with `ESC[1C` (cursor forward) rather than space
characters. The controller stripped CSI sequences and substituted nothing, so
the visible text it matched against was welded together:

```text
>You are in C:\...\trigger-workspace Doyoutrustthecontentsofthisdirectory?
```

Every literal the trust gate depended on failed: the trust question, the
`> You are in <path>` header regex, the explanation paragraph, and the option
labels (`2. No, quit` rendered as `2No, qi`). The exact-trust-prompt check could
never return true, so a perfectly normal trust screen was classified as an
unexpected path.

## 2. An update screen preceded the trust screen

Executable discovery resolved PATH-first to the npm shim, which opened with an
update prompt containing `Press enter to continue` and no trust question. The
controller's unexpected-prompt heuristic fired immediately and stopped before
anything else could happen.

## 3. A model-deprecation interstitial

The pinned trigger model produced a blocking modal offering its replacement.
`model/list` marks a superseded model with a non-null `upgrade` pointer, so this
screen was predictable from data the runtime already exposed. Dynamic model
selection now excludes such models by construction.

## 4. The turn-activity pattern matched nothing

Turn detection searched for `Working (<n>s ... to interrupt)`. The literal
`Working (` does not exist in the binary; the status line is assembled from the
fragments `(`, ` • `, and ` to interrupt)`. Success could never be recognised,
which was the one signal the design existed to produce.

## What replaced it

The app-server exposes `thread/start` and `turn/start` in its stable method
tier, on the same local process, handshake, and Codex-owned authentication that
observation already uses. A live experiment confirmed that one such turn anchors
the five-hour subscription window: before it the reset timestamp advanced with
wall time at a slope of 1.004 with distance pinned at about 17998 seconds, and
after it the timestamp held fixed within one second across 62 seconds while the
distance decreased.

None of the four blockers above can occur on a protocol that renders nothing.
