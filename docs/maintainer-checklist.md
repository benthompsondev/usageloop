# Maintainer checklist for 1.3.9

Updated after PR #4 merged at `8b5ad0a`. Localization and its correction are
already on main with hefengq's original commit and credit preserved. The steps
below concern only the remaining release candidate.

## 1. Review and authorize the candidate PR

Review `codex/release-candidate-1.3.9` against current main:

```powershell
git log --oneline origin/main..codex/release-candidate-1.3.9
git diff origin/main...codex/release-candidate-1.3.9
```

The diff should contain the remaining-quota display, regression tests, release
metadata/docs, dashboard previews and the 1.3.8 upgrade CI scenario. The original
`codex/community-1.3.9` branch is a preserved checkpoint, not the branch to push.
Read the [candidate review](community-release-review.md) for validation limits.

Approve pushing the clean candidate and opening a new PR against main. The
normal privacy, voice, review and push gates still apply. No push or new PR has
been made during this reconciliation. If remote main has moved, reconcile it
before publishing.

Suggested PR title: **Show remaining quota and prepare 1.3.9**.

Suggested description:

> Show remaining allowance in both green bars while keeping consumed percentages
> visible. Unknown readings stay unchecked, and expired readings ask for Sync
> rather than pretending quota refilled. Quota calculations and request
> safeguards remain unchanged. The last-sync label uses the latest quota reading.
>
> Includes regression tests, 1.3.9 metadata and release notes, updated dashboard
> previews, and CI upgrade coverage from 1.3.8. Based on main after PR #4 merged;
> its localization commits are already included in the base history.
>
> Local validation: 598 tests with 10 skips, compile/help checks, native Windows
> previews, a Windows executable build and provider-free activation checks.
> Candidate CI and packaged Windows/Linux acceptance remain release gates.

## 2. Run CI and finish packaged acceptance

Allow any workflow approvals required for the new PR only after reviewing its
actual head and workflow diff. Wait for `validate` and `verify`, including both
platform suites, both packages and all Windows installation scenarios. Confirm
that `windows-install-acceptance (1.3.8)` runs successfully.

If the native IPC test crash recurs, retain the actual failure logs and diagnose
it. A passing rerun does not explain a previous failure.

Use the candidate's CI artifacts in disposable environments:

- Windows: check fresh install and upgrade from 1.3.8. Preserve seeded settings,
  history and guarded attempts. Choose English, Chinese and system language;
  fully exit from the tray and check the saved choice after restart.
- Linux x86_64: open the package on a real desktop with a CJK font. Check dialogs,
  Settings and tray behavior, or normal window/exit behavior without a tray.
- Both: inspect normal, exhausted, unknown, expired and weekly-protected quota.
  Sync must stay read-only. Use synthetic data for screenshots. Do not enable
  automation solely for acceptance or start a real window without approval.

The local Windows executable checks passed. A local installer compiler and a
usable Linux runtime were unavailable. The baseline's green CI packages do not
replace CI and acceptance for this candidate.

## 3. Review and merge the candidate

Review the final diff and commit list, resolve real findings, then approve
merging when checks and packaged acceptance pass. Confirm the exact merged
commit matches the accepted code. Use artifacts built from that final commit;
rebuild and verify if the code changed.

## 4. Approve and publish the release

Confirm version **1.3.9** and review the
[draft release notes](1.3.9-release-notes.md). On GitHub choose
**Releases → Draft a new release**, create `v1.3.9` at the verified final commit,
and attach its four matching assets:

1. `UsageLoop-Setup.exe`
2. `UsageLoop-Setup.exe.sha256`
3. `UsageLoop-1.3.9-linux-x86_64.tar.gz`
4. `UsageLoop-1.3.9-linux-x86_64.tar.gz.sha256`

Verify checksums and version 1.3.9 in both packages. Review the draft and
contributor credit, then explicitly approve **Publish release**. Verify public
downloads and updater metadata afterward. Update pinned README/website download
links only when the release exists.

## 5. Respond to the issues

### Issue #2: ask for evidence and leave it open

Proposed Chinese reply:

> 你好！截图中的“未确认 / 不会重试”表示 UsageLoop 无法确认这次启动的结果。
> 请求可能已经发出，为避免重复消耗额度，程序不会重试同一次启动。仅凭这张截图，
> 还不能确定为什么没有确认成功。
>
> 请补充这几项信息：
> 1. UsageLoop 版本、Windows/Linux 版本、Codex 客户端及其版本；UsageLoop 是否为自行修改的版本？
> 2. 对应的“最近启动记录”（或你界面中的“预热记录”），以及“技术详情 → 复制此摘要”的内容。
> 3. 你当时预期发生什么？之后 Codex 的重置时间是否改变？大约在什么时间？
>
> 分享前请去掉账号、私人路径等信息，不要发送登录凭据、令牌、对话或完整日志。
> “同步用量”只读取用量，不会重新发送这次启动请求。

This requests the missing evidence without claiming Sync can clear an uncertain
attempt or diagnosing an unverified compatibility failure.

### Issue #3: close after the release includes it

Proposed Chinese reply after publication:

> 谢谢建议！1.3.9 的五小时和每周绿色进度条现在都显示剩余额度，旁边仍保留已用百分比。
> 未知读数会显示为尚未检查；旧读数不会在重置时间经过后自动变成满额，而是提示同步更新。
> 每周额度保护和不会重试不明确请求的规则保持不变。
>
> 简体中文界面也已加入同一个 Windows/Linux 应用，感谢 hefengq 的本地化贡献。
> 可在“设置 → 语言”中选择，完全退出后重新打开生效。

Add the verified release link, post the reply, then close Issue #3 as completed.
Keep Issue #2 open until its evidence supports a diagnosis or a documented
resolution. Public replies and closing issues each need approval.
