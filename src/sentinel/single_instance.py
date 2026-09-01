"""Per-user guard that keeps one UsageLoop scheduler alive at a time."""

from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
from ctypes import wintypes

from PySide6.QtCore import QIODevice, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


ERROR_ALREADY_EXISTS = 183
ACTIVATION_MESSAGE = b"activate\n"
ACTIVATION_ACK = b"\x06"


class SingleInstanceGuard:
    def __init__(self, name: str, *, lock_root: Path | None = None):
        self.name = name
        self._handle: int | None = None
        self._lock_file = None
        self._lock_root = lock_root

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        if os.name != "nt":
            return self._acquire_file_lock()

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        ctypes.set_last_error(0)
        handle = create_mutex(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            raise ctypes.WinError(error)
        if error == ERROR_ALREADY_EXISTS:
            close_handle(handle)
            return False
        self._handle = int(handle)
        return True

    def _acquire_file_lock(self) -> bool:
        import fcntl

        root = self._lock_root or _linux_lock_root()
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        digest = hashlib.sha256(self.name.encode("utf-8")).hexdigest()[:24]
        path = root / f"{digest}.lock"
        lock_file = path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        self._lock_file = lock_file
        self._handle = lock_file.fileno()
        return True

    def close(self) -> None:
        if self._handle is None:
            return
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL
            close_handle(self._handle)
        elif self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None
        self._handle = None

    def __enter__(self) -> "SingleInstanceGuard":
        if not self.acquire():
            raise RuntimeError("UsageLoop is already running.")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class ActivationChannel(QObject):
    """Per-user local IPC used only to restore the existing desktop window."""

    activation_requested = Signal()

    def __init__(self, name: str):
        super().__init__()
        # Qt's Windows local-socket backend has a short server-name ceiling;
        # keep the deterministic pipe name comfortably below it.
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
        self.name = f"UsageLoop-{digest}-activation"
        self._server: QLocalServer | None = None
        self._clients: set[QLocalSocket] = set()
        self._buffers: dict[QLocalSocket, bytes] = {}

    def start_primary(self) -> bool:
        self.close()
        # The caller owns the process mutex before this method is called, so no
        # live UsageLoop server can legitimately own this endpoint. Removing it
        # recovers the named-pipe residue left by an abnormal process death.
        QLocalServer.removeServer(self.name)
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        server.newConnection.connect(self._accept_connections)
        if not server.listen(self.name):
            server.deleteLater()
            return False
        self._server = server
        return True

    def activate_existing(self, timeout_ms: int = 1_000) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self.name, QIODevice.OpenModeFlag.ReadWrite)
        if not socket.waitForConnected(timeout_ms):
            socket.abort()
            return False
        if socket.write(ACTIVATION_MESSAGE) != len(ACTIVATION_MESSAGE):
            socket.abort()
            return False
        socket.flush()
        if socket.bytesToWrite():
            socket.waitForBytesWritten(timeout_ms)
            if socket.bytesToWrite():
                socket.abort()
                return False
        if not socket.waitForReadyRead(timeout_ms):
            socket.abort()
            return False
        if bytes(socket.readAll()) != ACTIVATION_ACK:
            socket.abort()
            return False
        socket.disconnectFromServer()
        return True

    def close(self) -> None:
        for socket in tuple(self._clients):
            socket.abort()
            socket.deleteLater()
        self._clients.clear()
        self._buffers.clear()
        if self._server is not None:
            self._server.close()
            self._server.deleteLater()
            self._server = None

    def _accept_connections(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            self._clients.add(socket)
            self._buffers[socket] = b""
            socket.readyRead.connect(lambda current=socket: self._read(current))
            socket.disconnected.connect(
                lambda current=socket: self._discard(current)
            )
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        payload = self._buffers.get(socket, b"") + bytes(socket.readAll())
        self._buffers[socket] = payload
        if b"\n" not in payload:
            return
        if payload == ACTIVATION_MESSAGE:
            socket.write(ACTIVATION_ACK)
            socket.flush()
            self.activation_requested.emit()

    def _discard(self, socket: QLocalSocket) -> None:
        self._clients.discard(socket)
        self._buffers.pop(socket, None)
        socket.deleteLater()


class InstanceCoordinator:
    """Keep one scheduler alive while allowing normal launches to show it."""

    def __init__(self, guard: SingleInstanceGuard, channel: ActivationChannel):
        self.guard = guard
        self.channel = channel

    def claim(self, *, background: bool) -> bool:
        # The per-user mutex or file lock owns scheduler authority.
        # ActivationChannel only restores the process that already owns it.
        if self.guard.acquire():
            # Failure to expose activation IPC must never allow a second
            # scheduler. The mutex remains authoritative and the primary runs.
            self.channel.start_primary()
            return True
        if not background:
            self.channel.activate_existing()
        return False


def _linux_lock_root() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    candidate = Path(runtime) if runtime else None
    if candidate is not None and candidate.is_absolute():
        return candidate / "usageloop"
    cache = os.environ.get("XDG_CACHE_HOME")
    candidate = Path(cache) if cache else None
    base = (
        candidate
        if candidate is not None and candidate.is_absolute()
        else Path.home() / ".cache"
    )
    return base / "usageloop" / "runtime"
