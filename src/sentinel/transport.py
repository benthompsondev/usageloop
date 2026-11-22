"""Own the local Codex app-server child process and line transport."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Iterable

from .host import is_windows
from .windows_codex import package_version, registered_codex_packages


class SentinelRuntimeError(RuntimeError):
    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class CodexNotFoundError(SentinelRuntimeError):
    def __init__(self, failures: tuple[str, ...] = ()):
        detail = "A usable Codex executable was not found."
        if failures:
            detail += " " + "; ".join(failures) + "."
        super().__init__("codex_not_found", detail)


class CodexOverrideError(SentinelRuntimeError):
    def __init__(self, reason: str):
        super().__init__(
            "codex_override_invalid",
            f"{CODEX_EXECUTABLE_ENV} is set, but {reason} "
            "Set it to a usable Codex executable or clear it and restart UsageLoop.",
        )


class CodexRuntimeChangedError(SentinelRuntimeError):
    def __init__(self):
        super().__init__("codex_runtime_changed", "The Codex runtime changed before the operation.")


class AppServerUnavailableError(SentinelRuntimeError):
    def __init__(self, message: str = "The local Codex app-server is unavailable."):
        super().__init__("app_server_unavailable", message)


class TransportTimeoutError(SentinelRuntimeError):
    def __init__(self):
        super().__init__("app_server_timeout", "The local Codex app-server did not respond in time.")


_EOF = object()


@dataclass(frozen=True)
class CodexRuntime:
    executable: Path
    source: str
    version: str
    skipped: tuple[str, ...] = ()

    @property
    def diagnostic(self) -> str:
        return f"{self.source} ({_diagnostic_path(self.executable)})"


def find_codex_executable(
    *,
    path_value: str | None = None,
    known_candidates: Iterable[Path] | None = None,
) -> Path:
    """Find an existing candidate without starting a child process."""
    return _select_runtime(path_value, known_candidates, check_version=False).executable


def discover_codex_runtime() -> CodexRuntime:
    """Resolve a runtime and verify its CLI can report a version."""
    return _select_runtime(None, None, check_version=True)


def _path_candidates(path_value: str | None) -> Iterable[tuple[str, Path]]:
    search_path = os.environ.get("PATH", "") if path_value is None else path_value
    names = ("codex.exe", "codex.cmd") if is_windows() else ("codex",)
    for name in names:
        for directory in search_path.split(os.pathsep):
            if directory:
                yield "PATH", Path(directory.strip('"')) / name


def _select_runtime(
    path_value: str | None,
    known_candidates: Iterable[Path] | None,
    *,
    check_version: bool,
) -> CodexRuntime:
    override = os.environ.get(CODEX_EXECUTABLE_ENV, "").strip()
    if override:
        try:
            executable = Path(override).expanduser().resolve(strict=True)
            if not executable.is_file():
                raise ValueError
        except (OSError, RuntimeError, ValueError):
            raise CodexOverrideError("the path does not name an accessible file.") from None
        if is_windows() and executable.suffix.lower() not in {".exe", ".cmd", ".bat"}:
            raise CodexOverrideError("the file is not an .exe, .cmd, or .bat executable.")
        try:
            version = _runtime_version(executable) if check_version else ""
        except (OSError, SentinelRuntimeError) as exc:
            reason = str(exc) if isinstance(exc, SentinelRuntimeError) else "The file is inaccessible."
            raise CodexOverrideError(f"the executable failed its version check. {reason}") from None
        return CodexRuntime(executable, "Explicit override", version)

    failures: list[str] = []
    seen: set[Path] = set()
    for source, candidate in _runtime_candidates(path_value, known_candidates):
        try:
            if not candidate.is_file():
                continue
            executable = candidate.resolve(strict=True)
            if executable in seen:
                continue
            seen.add(executable)
            version = _runtime_version(executable) if check_version else ""
        except (OSError, RuntimeError, ValueError) as exc:
            reason = str(exc) if isinstance(exc, SentinelRuntimeError) else "File inaccessible."
            failure = f"{source}: {reason.rstrip('.')}"
            if failure not in failures:
                failures.append(failure)
            continue
        return CodexRuntime(executable, source, version, tuple(failures))
    raise CodexNotFoundError(tuple(failures))


def _runtime_version(executable: Path) -> str:
    metadata = executable.stat()
    version, error = _checked_version(
        executable, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
        int(time.monotonic() // 60),
    )
    if error:
        raise AppServerUnavailableError(error)
    return version


@lru_cache(maxsize=16)
def _checked_version(
    executable: Path, size: int, modified: int, changed: int, minute: int
) -> tuple[str, str]:
    # Bound repeated subprocess launches on the desktop polling path. Retry a
    # failure next minute or right away if an installer replaces the file.
    try:
        version = read_codex_version(executable, timeout=2.0)
    except AppServerUnavailableError as exc:
        return "", str(exc)
    if not re.fullmatch(r"codex(?:-cli)? [0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.+-]+)?", version):
        return "", "Codex returned an unrecognized version string."
    return version, ""


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
    except subprocess.TimeoutExpired as exc:
        raise AppServerUnavailableError("The Codex version check timed out.") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise AppServerUnavailableError("The Codex version command could not be started.") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise AppServerUnavailableError("The Codex version command returned an error or no version.")
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

#: Process-local override for installations that automatic discovery cannot derive.
CODEX_EXECUTABLE_ENV = "USAGELOOP_CODEX_EXECUTABLE"


def _runtime_candidates(
    path_value: str | None, known_candidates: Iterable[Path] | None
) -> Iterable[tuple[str, Path]]:
    if known_candidates is not None:
        yield from (("Native runtime", path) for path in known_candidates)
    elif is_windows():
        yield from _windows_runtime_candidates()
    else:
        yield from (("Linux native runtime", path) for path in _linux_known_candidates(path_value=path_value))
    yield from _path_candidates(path_value)


def _windows_runtime_candidates() -> Iterable[tuple[str, Path]]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    root = Path(local_app_data) / "OpenAI" / "Codex" if local_app_data else None
    manual = root / "manual-cli" if root is not None else None
    for version, package in registered_codex_packages():
        # Desktop repairs are stored by desktop package version. A repair
        # for a different package must not shadow the registered bundle.
        if manual is not None and package_version(version):
            yield "Desktop repair matching MSIX", manual / version / "codex.exe"
        yield "Registered Codex MSIX", package / "app" / "resources" / "codex.exe"
    if root is not None and manual is not None:
        repaired = (path for path in manual.glob("*/codex.exe") if package_version(path.parent.name))
        for path in sorted(repaired, key=lambda path: package_version(path.parent.name), reverse=True):
            yield "Versioned desktop repair", path
        yield "Unversioned desktop repair", manual / "codex.exe"
        yield from (("Managed Codex bin", path) for path in _newest_first((root / "bin").glob("*/codex.exe")))


def _diagnostic_path(executable: Path) -> str:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if is_windows() and local_app_data:
        root = Path(local_app_data) / "OpenAI" / "Codex"
        if executable.is_relative_to(root):
            parts = executable.relative_to(root).parts
            if (
                parts == ("manual-cli", "codex.exe")
                or len(parts) == 3 and parts[2] == "codex.exe" and (
                    parts[0] == "manual-cli" and package_version(parts[1])
                    or parts[0] == "bin" and re.fullmatch(r"[0-9a-f]{8,64}", parts[1])
                )
            ):
                return "%LOCALAPPDATA%\\OpenAI\\Codex\\" + "\\".join(parts)
    if executable.name.lower() in {"codex", "codex.exe", "codex.cmd", "codex.bat"}:
        return f"<path hidden>/{executable.name}"
    return "<path hidden>"


def _linux_known_candidates(*, path_value: str | None = None) -> tuple[Path, ...]:
    """Return installed Linux binaries, newest first.

    Both supported Linux installs expose the same executable, byte for byte.
    The Codex CLI manages one under `CODEX_HOME`, and the Codex desktop app
    ships an identical one inside its package. Neither is guaranteed to be on
    PATH under the name `codex`, so PATH alone is not a sufficient search.
    """
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
    return _newest_first(discovered)


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
