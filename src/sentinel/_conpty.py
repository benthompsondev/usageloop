"""Small Windows ConPTY runner used only for the interactive Codex trigger."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Sequence


class ConPtyError(RuntimeError):
    pass


@dataclass(frozen=True)
class ConPtyResult:
    runtime_seconds: float
    exit_code: int
    ended_early: bool


if os.name == "nt":
    import msvcrt

    class _COORD(ctypes.Structure):
        _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", wintypes.LPVOID),
            ("bInheritHandle", wintypes.BOOL),
        ]


    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]


    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", _STARTUPINFOW),
            ("lpAttributeList", wintypes.LPVOID),
        ]


    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]


_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_HANDLE_FLAG_INHERIT = 0x00000001
_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_STILL_ACTIVE = 259


def conpty_available() -> bool:
    if os.name != "nt":
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return all(
        hasattr(kernel32, name)
        for name in (
            "CreatePseudoConsole",
            "ClosePseudoConsole",
            "InitializeProcThreadAttributeList",
            "UpdateProcThreadAttribute",
        )
    )


def run_conpty(
    command: Sequence[str],
    *,
    cwd: Path,
    min_runtime_seconds: float,
    quiet_seconds: float,
    max_runtime_seconds: float,
    exit_grace_seconds: float,
) -> ConPtyResult:
    if not conpty_available():
        raise ConPtyError("Windows ConPTY is unavailable on this system.")
    if not command:
        raise ConPtyError("The interactive command is empty.")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _configure_kernel32(kernel32)
    input_read = wintypes.HANDLE()
    input_write = wintypes.HANDLE()
    output_read = wintypes.HANDLE()
    output_write = wintypes.HANDLE()
    pseudoconsole = ctypes.c_void_p()
    process_info = _PROCESS_INFORMATION()
    attribute_buffer = None
    attribute_initialized = False
    reader: threading.Thread | None = None
    input_fd: int | None = None
    output_fd: int | None = None

    security = _SECURITY_ATTRIBUTES(
        ctypes.sizeof(_SECURITY_ATTRIBUTES),
        None,
        True,
    )
    if not kernel32.CreatePipe(ctypes.byref(input_read), ctypes.byref(input_write), ctypes.byref(security), 0):
        raise _last_error("Could not create the ConPTY input pipe")
    try:
        if not kernel32.CreatePipe(ctypes.byref(output_read), ctypes.byref(output_write), ctypes.byref(security), 0):
            raise _last_error("Could not create the ConPTY output pipe")
        if not kernel32.SetHandleInformation(input_write, _HANDLE_FLAG_INHERIT, 0):
            raise _last_error("Could not protect the ConPTY input handle")
        if not kernel32.SetHandleInformation(output_read, _HANDLE_FLAG_INHERIT, 0):
            raise _last_error("Could not protect the ConPTY output handle")

        result = kernel32.CreatePseudoConsole(
            _COORD(100, 30), input_read, output_write, 0, ctypes.byref(pseudoconsole)
        )
        if result != 0:
            raise ConPtyError(f"CreatePseudoConsole failed with HRESULT 0x{result & 0xFFFFFFFF:08x}.")
        _close_handle(kernel32, input_read)
        input_read = wintypes.HANDLE()
        _close_handle(kernel32, output_write)
        output_write = wintypes.HANDLE()

        attribute_size = ctypes.c_size_t()
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
        attribute_list = ctypes.cast(attribute_buffer, wintypes.LPVOID)
        if not kernel32.InitializeProcThreadAttributeList(
            attribute_list, 1, 0, ctypes.byref(attribute_size)
        ):
            raise _last_error("Could not initialize ConPTY process attributes")
        attribute_initialized = True
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            _PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            pseudoconsole,
            ctypes.sizeof(ctypes.c_void_p),
            None,
            None,
        ):
            raise _last_error("Could not attach the pseudo console")

        startup = _STARTUPINFOEXW()
        startup.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        startup.lpAttributeList = attribute_list
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(command)))
        application = str(Path(command[0]))
        if not kernel32.CreateProcessW(
            application,
            command_line,
            None,
            None,
            False,
            _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW,
            None,
            str(cwd),
            ctypes.cast(ctypes.byref(startup), wintypes.LPVOID),
            ctypes.byref(process_info),
        ):
            raise _last_error("Could not start the interactive Codex process")
        _close_handle(kernel32, process_info.hThread)
        process_info.hThread = wintypes.HANDLE()

        input_fd = msvcrt.open_osfhandle(int(input_write.value), os.O_WRONLY | os.O_BINARY)
        input_write = wintypes.HANDLE()
        output_fd = msvcrt.open_osfhandle(int(output_read.value), os.O_RDONLY | os.O_BINARY)
        output_read = wintypes.HANDLE()

        started = time.monotonic()
        activity = [started]
        reader = threading.Thread(
            target=_drain_output,
            args=(output_fd, activity),
            name="sentinel-codex-conpty-output",
            daemon=True,
        )
        reader.start()
        output_fd = None

        ended_early = False
        while True:
            now = time.monotonic()
            wait_result = kernel32.WaitForSingleObject(process_info.hProcess, 100)
            if wait_result == _WAIT_OBJECT_0:
                ended_early = now - started < min_runtime_seconds
                break
            if wait_result != _WAIT_TIMEOUT:
                raise _last_error("Could not monitor the interactive Codex process")
            elapsed = now - started
            if elapsed >= max_runtime_seconds:
                break
            if elapsed >= min_runtime_seconds and now - activity[0] >= quiet_seconds:
                break

        if kernel32.WaitForSingleObject(process_info.hProcess, 0) != _WAIT_OBJECT_0:
            _write_control_c(input_fd)
            deadline = time.monotonic() + exit_grace_seconds
            resent = False
            while kernel32.WaitForSingleObject(process_info.hProcess, 100) == _WAIT_TIMEOUT:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    kernel32.TerminateProcess(process_info.hProcess, 1)
                    kernel32.WaitForSingleObject(process_info.hProcess, 1000)
                    break
                if not resent and remaining <= exit_grace_seconds / 2:
                    _write_control_c(input_fd)
                    resent = True

        exit_code = wintypes.DWORD(_STILL_ACTIVE)
        if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
            raise _last_error("Could not read the interactive Codex exit code")
        runtime = time.monotonic() - started
        return ConPtyResult(runtime, int(exit_code.value), ended_early)
    finally:
        if input_fd is not None:
            try:
                os.close(input_fd)
            except OSError:
                pass
        if output_fd is not None:
            try:
                os.close(output_fd)
            except OSError:
                pass
        _close_handle(kernel32, process_info.hThread)
        _close_handle(kernel32, process_info.hProcess)
        if attribute_initialized and attribute_buffer is not None:
            kernel32.DeleteProcThreadAttributeList(ctypes.cast(attribute_buffer, wintypes.LPVOID))
        if pseudoconsole.value:
            kernel32.ClosePseudoConsole(pseudoconsole)
        if reader is not None:
            reader.join(timeout=1.0)
        _close_handle(kernel32, input_read)
        _close_handle(kernel32, input_write)
        _close_handle(kernel32, output_read)
        _close_handle(kernel32, output_write)


def _drain_output(output_fd: int, activity: list[float]) -> None:
    try:
        while True:
            chunk = os.read(output_fd, 4096)
            if not chunk:
                return
            activity[0] = time.monotonic()
    except OSError:
        return
    finally:
        try:
            os.close(output_fd)
        except OSError:
            pass


def _write_control_c(input_fd: int) -> None:
    try:
        os.write(input_fd, b"\x03")
    except OSError:
        pass


def _configure_kernel32(kernel32: ctypes.WinDLL) -> None:
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    kernel32.SetHandleInformation.restype = wintypes.BOOL
    kernel32.CreatePseudoConsole.argtypes = [
        _COORD,
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long
    kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.c_size_t,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _close_handle(kernel32: ctypes.WinDLL, handle: wintypes.HANDLE | int | None) -> None:
    value = handle if isinstance(handle, int) else getattr(handle, "value", None)
    if value:
        kernel32.CloseHandle(wintypes.HANDLE(value))


def _last_error(prefix: str) -> ConPtyError:
    code = ctypes.get_last_error()
    return ConPtyError(f"{prefix} (Windows error {code}).")
