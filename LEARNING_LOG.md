# Learning Log

## 2026-08-29

- **Did:** Built the Phase 1 observer test-first, added local setup, and verified it through the real app-server.
- **Learned:** The native app Codex can be newer than the npm shim; live selection must report the executable it actually uses.
- **Verified with:** 31 deterministic tests, compile/CLI checks, live doctor, and a four-sample session that found a fixed reset timestamp.
- **Blocked on:** Nothing in the Phase 1 observer scope.
- **Next:** Treat this commit as the observation foundation when the later window-chaining product is designed.
