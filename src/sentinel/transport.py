"""Own the local Codex app-server child process and line transport."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import threading
from typing import Iterable


class SentinelRuntimeError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class CodexNotFoundError(SentinelRuntimeError):
    def __init__(self):
        super().__init__("codex_not_found", "A usable Codex executable was not found.")


class AppServerUnavailableError(SentinelRuntimeError):
    def __init__(self, message: str = "The local Codex app-server is unavailable."):
        super().__init__("app_server_unavailable", message)


class TransportTimeoutError(SentinelRuntimeError):
    def __init__(self):
        super().__init__("app_server_timeout", "The local Codex app-server did not respond in time.")


_EOF = object()


def find_codex_executable(
    *,
    path_value: str | None = None,
    known_candidates: Iterable[Path] | None = None,
) -> Path:
    """Prefer the installed native Codex binary, then PATH, then a command shim.

    The npm shim is often an older release than the native app binary. Sentinel
    depends on evolving app-server behavior, so it must not silently run the
    stale one: a locally installed shim measured five minor versions behind the
    native executable it shadowed on PATH.

    The same rule decides Linux. A Codex desktop install ships its own
    `codex` and puts nothing on PATH, so a PATH-only search reports "not
    installed" on a machine that plainly has Codex.
    """
    candidates = _default_known_candidates() if known_candidates is None else tuple(known_candidates)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    search_path = os.environ.get("PATH", "") if path_value is None else path_value
    names = ("codex.exe", "codex.cmd") if os.name == "nt" else ("codex",)
    for name in names:
        for directory in search_path.split(os.pathsep):
            if not directory:
                continue
            candidate = Path(directory.strip('"')) / name
            if candidate.is_file():
                return candidate.resolve()
    raise CodexNotFoundError()


def read_codex_version(executable: Path, timeout: float = 10.0) -> str:
    try:
        result = subprocess.run(
            build_codex_command(executable, "--version"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppServerUnavailableError("Codex was found but its version could not be read.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise AppServerUnavailableError("Codex was found but its version command failed.")
    return result.stdout.strip()


class CodexProcessTransport:
    """Newline-delimited JSON transport over a child `codex app-server`."""

    def __init__(self, executable: Path):
        self.executable = executable
        self._process: subprocess.Popen[str] | None = None
        self._stdout: queue.Queue[str | object] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self.stderr_seen = False

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                build_codex_command(self.executable, "app-server", "--stdio"),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            raise AppServerUnavailableError() from exc
        self._threads = [
            threading.Thread(target=self._read_stdout, name="sentinel-app-server-output", daemon=True),
            threading.Thread(target=self._drain_stderr, name="sentinel-app-server-errors", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def send_line(self, line: str) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerUnavailableError()
        try:
            process.stdin.write(line + "\n")
            process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise AppServerUnavailableError() from exc

    def read_line(self, timeout: float | None = None) -> str:
        try:
            value = self._stdout.get(timeout=timeout)
        except queue.Empty as exc:
            raise TransportTimeoutError() from exc
        if value is _EOF:
            raise AppServerUnavailableError("The local Codex app-server exited unexpectedly.")
        return str(value)

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        finally:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            for thread in self._threads:
                thread.join(timeout=1)
            self._threads = []

    def _read_stdout(self) -> None:
        process = self._process
        stream = process.stdout if process is not None else None
        if stream is None:
            self._stdout.put(_EOF)
            return
        try:
            for line in stream:
                self._stdout.put(line.rstrip("\r\n"))
        finally:
            self._stdout.put(_EOF)

    def _drain_stderr(self) -> None:
        process = self._process
        stream = process.stderr if process is not None else None
        if stream is None:
            return
        for _line in stream:
            self.stderr_seen = True

    def __enter__(self) -> CodexProcessTransport:
        self.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def build_codex_command(executable: Path, *arguments: str) -> list[str]:
    """Build a CreateProcess-safe command for native binaries and Windows shims."""
    if os.name == "nt" and executable.suffix.lower() in {".cmd", ".bat"}:
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(executable), *arguments]
    return [str(executable), *arguments]


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


#: Names the Codex desktop app puts on PATH. Measured: the `chatgpt` package
#: installs `/usr/bin/chatgpt` as a symlink into its own installation.
_LINUX_DESKTOP_LAUNCHERS = ("chatgpt",)

#: The one install prefix that has actually been measured, kept only so a
#: session with a stripped PATH still finds an installed desktop app. Guessing
#: further prefixes would imply coverage that has never been verified; a Codex
#: installed anywhere else is reached through `CODEX_EXECUTABLE_ENV`.
_LINUX_DESKTOP_PREFIXES = ("/usr/lib/chatgpt",)

#: Last-resort override for a Codex this module cannot derive, such as a Nix,
#: Flatpak or hand-built layout. Automatic discovery is the normal path; this
#: is read, never written, and never required when Codex is installed normally.
CODEX_EXECUTABLE_ENV = "USAGELOOP_CODEX_EXECUTABLE"


def _default_known_candidates() -> tuple[Path, ...]:
    if os.name == "nt":
        return _windows_known_candidates()
    return _linux_known_candidates()


def _windows_known_candidates() -> tuple[Path, ...]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return ()
    bin_root = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    if not bin_root.is_dir():
        return ()
    return _newest_first(bin_root.glob("*/codex.exe"))


def _linux_known_candidates(*, path_value: str | None = None) -> tuple[Path, ...]:
    """Return an explicit override first, then installed binaries, newest first.

    Both supported Linux installs expose the same executable, byte for byte.
    The Codex CLI manages one under `CODEX_HOME`, and the Codex desktop app
    ships an identical one inside its package. Neither is guaranteed to be on
    PATH under the name `codex`, so PATH alone is not a sufficient search.
    """
    override = os.environ.get(CODEX_EXECUTABLE_ENV, "").strip()
    leading: tuple[Path, ...] = ()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            leading = (candidate,)

    codex_home = os.environ.get("CODEX_HOME", "").strip()
    home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    discovered = [home / "plugins" / ".plugin-appserver" / "codex"]
    discovered.extend(
        root / "resources" / "codex"
        for root in _linux_desktop_app_roots(path_value=path_value)
    )
    discovered.extend(
        Path(prefix) / "resources" / "codex" for prefix in _LINUX_DESKTOP_PREFIXES
    )
    return leading + _newest_first(discovered)


def _linux_desktop_app_roots(*, path_value: str | None = None) -> tuple[Path, ...]:
    """Locate a Codex desktop installation the way its own launcher does.

    The launcher the package puts on PATH is a symlink into the installation,
    and the script it points at resolves itself to find its own directory:

        exec "$(dirname "$(readlink -f "$0")")/ChatGPT" "$@"

    Following that same symlink is therefore a derivation from what is actually
    installed, not a guess at an install prefix, and it keeps working wherever
    the app is installed. A `chatgpt` on PATH that is something else simply has
    no `resources/codex` beside it and drops out of the candidate list.
    """
    search_path = os.environ.get("PATH", "") if path_value is None else path_value
    roots: list[Path] = []
    for name in _LINUX_DESKTOP_LAUNCHERS:
        for directory in search_path.split(os.pathsep):
            if not directory:
                continue
            launcher = Path(directory.strip('"')) / name
            try:
                if not launcher.is_file():
                    continue
                root = launcher.resolve().parent
            except OSError:
                continue
            if root not in roots:
                roots.append(root)
    return tuple(roots)


def _newest_first(candidates: Iterable[Path]) -> tuple[Path, ...]:
    """Rank existing candidates newest first, keeping each real file once.

    A derived desktop root and the measured fallback prefix can name the same
    binary, so candidates are collapsed by resolved path rather than by the
    spelling that found them.
    """
    measured: list[tuple[float, Path]] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        seen.add(resolved)
        measured.append((mtime, candidate))
    measured.sort(key=lambda item: item[0], reverse=True)
    return tuple(candidate for _mtime, candidate in measured)
