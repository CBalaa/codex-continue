#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

AUTO_SEND_DELAY_SECONDS = 0.2
CHAR_SEND_INTERVAL_SECONDS = 0.03
QUEUE_KEY = b"\t"
DISABLE_GUARD_SECONDS = 1.0
CTRL_C = 3
ESC = 27
LOCALHOST = "127.0.0.1"
DEFAULT_NTFY_BASE_URL = "https://ntfy.sh"
DEFAULT_NOTIFY_TIMEOUT_MS = 3000
NTFY_NOTIFICATION_TITLE = "Codex turn complete"

if os.name == "nt":
    import ctypes
    import msvcrt
    from ctypes import wintypes
else:
    import fcntl
    import pty
    import select
    import signal
    import termios
    import tty


if os.name == "nt":
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    STD_INPUT_HANDLE = ctypes.c_ulong(-10).value
    STD_OUTPUT_HANDLE = ctypes.c_ulong(-11).value
    ENABLE_PROCESSED_INPUT = 0x0001
    ENABLE_LINE_INPUT = 0x0002
    ENABLE_ECHO_INPUT = 0x0004
    ENABLE_QUICK_EDIT_MODE = 0x0040
    ENABLE_EXTENDED_FLAGS = 0x0080
    ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
    ENABLE_PROCESSED_OUTPUT = 0x0001
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258
    ERROR_BROKEN_PIPE = 109
    ERROR_INVALID_HANDLE = 6
    CP_UTF8 = 65001

    class COORD(ctypes.Structure):
        _fields_ = [
            ("X", ctypes.c_short),
            ("Y", ctypes.c_short),
        ]

    class SMALL_RECT(ctypes.Structure):
        _fields_ = [
            ("Left", ctypes.c_short),
            ("Top", ctypes.c_short),
            ("Right", ctypes.c_short),
            ("Bottom", ctypes.c_short),
        ]

    class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", COORD),
            ("dwCursorPosition", COORD),
            ("wAttributes", wintypes.WORD),
            ("srWindow", SMALL_RECT),
            ("dwMaximumWindowSize", COORD),
        ]

    LPBYTE = ctypes.POINTER(wintypes.BYTE)

    class STARTUPINFOW(ctypes.Structure):
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
            ("lpReserved2", LPBYTE),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", STARTUPINFOW),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL
    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL
    kernel32.GetConsoleCP.argtypes = []
    kernel32.GetConsoleCP.restype = wintypes.UINT
    kernel32.SetConsoleCP.argtypes = [wintypes.UINT]
    kernel32.SetConsoleCP.restype = wintypes.BOOL
    kernel32.GetConsoleOutputCP.argtypes = []
    kernel32.GetConsoleOutputCP.restype = wintypes.UINT
    kernel32.SetConsoleOutputCP.argtypes = [wintypes.UINT]
    kernel32.SetConsoleOutputCP.restype = wintypes.BOOL
    kernel32.GetConsoleScreenBufferInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO),
    ]
    kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
    kernel32.CreatePipe.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.POINTER(wintypes.HANDLE),
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.CreatePipe.restype = wintypes.BOOL
    if hasattr(kernel32, "CreatePseudoConsole"):
        kernel32.CreatePseudoConsole.argtypes = [
            COORD,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        kernel32.CreatePseudoConsole.restype = ctypes.c_long
        kernel32.ResizePseudoConsole.argtypes = [wintypes.HANDLE, COORD]
        kernel32.ResizePseudoConsole.restype = ctypes.c_long
        kernel32.ClosePseudoConsole.argtypes = [wintypes.HANDLE]
        kernel32.ClosePseudoConsole.restype = None
    kernel32.InitializeProcThreadAttributeList.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOEXW),
        ctypes.POINTER(PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    kernel32.WriteFile.restype = wintypes.BOOL


@dataclass(frozen=True)
class NtfyConfig:
    topic: str
    base_url: str
    timeout_seconds: float


def build_notify_override(
    python_argv: list[str], notifier_path: Path, host: str, port: int
) -> str:
    notify_argv = [*python_argv, str(notifier_path), host, str(port)]
    return f"notify={json.dumps(notify_argv)}"


def current_python_argv() -> list[str]:
    if sys.executable:
        return [sys.executable]
    fallback = shutil.which("python3") or shutil.which("python") or "python3"
    return [fallback]


def create_notify_socket() -> tuple[socket.socket, str, int]:
    notify_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    notify_socket.bind((LOCALHOST, 0))
    host, port = notify_socket.getsockname()
    return notify_socket, host, port


def build_ntfy_config(args: argparse.Namespace) -> Optional[NtfyConfig]:
    topic = (args.ntfy_topic or "").strip()
    if not topic:
        return None

    base_url = (args.ntfy_base_url or DEFAULT_NTFY_BASE_URL).strip()
    if not base_url:
        raise ValueError("--ntfy-base-url must not be empty when --ntfy-topic is set")

    parsed = urllib_parse.urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("--ntfy-base-url must be a valid http(s) URL")

    timeout_ms = args.notify_timeout_ms or DEFAULT_NOTIFY_TIMEOUT_MS
    return NtfyConfig(
        topic=topic,
        base_url=base_url.rstrip("/"),
        timeout_seconds=timeout_ms / 1000.0,
    )


def coerce_notification_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None

    normalized = value.replace("\r\n", "\n").strip()
    return normalized or None


def last_input_message(event: dict[str, object]) -> Optional[str]:
    input_messages = event.get("input-messages")
    if not isinstance(input_messages, list):
        return None

    for message in reversed(input_messages):
        normalized = coerce_notification_text(message)
        if normalized is not None:
            return normalized

    return None


def last_assistant_message(event: dict[str, object]) -> str:
    message = coerce_notification_text(event.get("last-assistant-message"))
    if message is None:
        return "turn complete but no assistant message"
    return message


def format_ntfy_message(event: dict[str, object]) -> str:
    lines: list[str] = []

    cwd = coerce_notification_text(event.get("cwd"))
    if cwd is not None:
        lines.append(f"cwd: {cwd}")

    user_message = last_input_message(event)
    if user_message is not None:
        if lines:
            lines.append("")
        lines.append("user:")
        lines.append(user_message)

    if lines:
        lines.append("")
    lines.append("assistant:")
    lines.append(last_assistant_message(event))
    return "\n".join(lines)


def build_ntfy_publish_url(base_url: str, topic: str) -> str:
    return f"{base_url}/{urllib_parse.quote(topic, safe='')}"


def send_ntfy_notification(config: NtfyConfig, event: dict[str, object]) -> None:
    request = urllib_request.Request(
        build_ntfy_publish_url(config.base_url, config.topic),
        data=format_ntfy_message(event).encode("utf-8"),
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Title": NTFY_NOTIFICATION_TITLE,
        },
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=config.timeout_seconds) as response:
        response.read()


def maybe_send_turn_notification(
    config: Optional[NtfyConfig], event: dict[str, object]
) -> None:
    if config is None:
        return

    try:
        send_ntfy_notification(config, event)
        debug(
            "sent ntfy notification before auto-continue "
            f"for topic {config.topic!r}"
        )
    except (OSError, ValueError, urllib_error.URLError) as error:
        debug(f"ntfy notification failed: {error}")


def bare_escape_pressed(data: bytes) -> bool:
    if data == b"\x1b":
        return True

    for index, value in enumerate(data):
        if value != ESC:
            continue
        next_byte = data[index + 1] if index + 1 < len(data) else None
        if next_byte in (ord("["), ord("O")):
            continue
        return True

    return False


def should_disable_auto_continue(data: bytes) -> bool:
    return CTRL_C in data or bare_escape_pressed(data)


def debug_enabled() -> bool:
    return os.environ.get("CODEX_AUTO_CONTINUE_DEBUG") == "1"


def debug_log_path() -> str:
    return os.environ.get(
        "CODEX_AUTO_CONTINUE_DEBUG_LOG",
        os.path.join(tempfile.gettempdir(), "codex-auto-continue-debug.log"),
    )


def debug(message: str) -> None:
    if not debug_enabled():
        return
    line = f"[codex-auto-continue][debug] {message}"
    try:
        with open(debug_log_path(), "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.write("\n")
    except OSError:
        pass
    try:
        os.write(sys.stderr.fileno(), f"\r\n{line}\r\n".encode("utf-8"))
    except OSError:
        pass


def schedule_send(prompt: str) -> list[tuple[float, bytes]]:
    queued: list[tuple[float, bytes]] = []
    send_at = time.monotonic() + AUTO_SEND_DELAY_SECONDS
    for ch in prompt:
        queued.append((send_at, ch.encode("utf-8")))
        send_at += CHAR_SEND_INTERVAL_SECONDS
    queued.append((send_at, QUEUE_KEY))
    return queued


def parse_positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error

    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")

    return parsed


def build_child_argv(
    node_bin: str,
    launcher_path: str,
    passthrough: list[str],
    notifier_path: Path,
    host: str,
    port: int,
) -> list[str]:
    return [
        node_bin,
        launcher_path,
        "--config",
        build_notify_override(current_python_argv(), notifier_path, host, port),
        *passthrough,
    ]


def launch_child_unix(args: argparse.Namespace, passthrough: list[str]) -> int:
    notifier_path = Path(__file__).with_name("codex-auto-continue-notify.py")
    notify_socket, host, port = create_notify_socket()
    notify_socket.setblocking(False)
    child_env = os.environ.copy()
    child_argv = build_child_argv(
        args.node,
        args.launcher,
        passthrough,
        notifier_path,
        host,
        port,
    )

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        os.execvpe(args.node, child_argv, child_env)

    try:
        return forward_loop_unix(
            child_pid,
            master_fd,
            notify_socket,
            args.prompt,
            args.limit,
            args.ntfy_config,
        )
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        notify_socket.close()


def copy_winsize_unix(source_fd: int, target_fd: int) -> None:
    try:
        packed = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(target_fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def forward_loop_unix(
    child_pid: int,
    master_fd: int,
    notify_socket: socket.socket,
    prompt: str,
    limit: Optional[int],
    ntfy_config: Optional[NtfyConfig],
) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stderr_fd = sys.stderr.fileno()
    stdin_is_tty = os.isatty(stdin_fd)
    old_tty_settings = None
    auto_continue_enabled = True
    remaining_sends = limit
    pending_send_bytes: list[tuple[float, bytes]] = []
    disable_allowed_at = time.monotonic() + DISABLE_GUARD_SECONDS

    if stdin_is_tty:
        old_tty_settings = termios.tcgetattr(stdin_fd)
        tty.setraw(stdin_fd)
        try:
            termios.tcflush(stdin_fd, termios.TCIFLUSH)
            debug("flushed pending stdin after launch-mode prompt")
        except termios.error as error:
            debug(f"stdin flush failed: {error}")
        copy_winsize_unix(stdin_fd, master_fd)
    else:
        debug("stdin is not a tty; disable hotkeys are unavailable")

    def on_sigwinch(_signum, _frame):
        if stdin_is_tty:
            copy_winsize_unix(stdin_fd, master_fd)

    previous_sigwinch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, on_sigwinch)

    disable_notice_shown = False
    limit_notice_shown = False

    try:
        while True:
            if pending_send_bytes and time.monotonic() >= pending_send_bytes[0][0]:
                _, chunk = pending_send_bytes.pop(0)
                os.write(master_fd, chunk)
                if chunk == QUEUE_KEY:
                    debug("sent queue key")
                    if remaining_sends is not None:
                        remaining_sends -= 1
                        debug(f"remaining auto-continue sends: {remaining_sends}")
                        if remaining_sends == 0:
                            auto_continue_enabled = False
                            if not limit_notice_shown:
                                os.write(
                                    stderr_fd,
                                    b"\r\n[codex-auto-continue] limit reached; disabled for this session.\r\n",
                                )
                                limit_notice_shown = True
                            debug("auto-continue limit reached")
                else:
                    debug(f"sent text chunk {chunk!r}")

            timeout = None
            if pending_send_bytes:
                timeout = max(0.0, pending_send_bytes[0][0] - time.monotonic())

            read_fds = [master_fd, notify_socket.fileno()]
            if stdin_is_tty:
                read_fds.append(stdin_fd)

            ready, _, _ = select.select(read_fds, [], [], timeout)

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(stdout_fd, data)

            if stdin_is_tty and stdin_fd in ready:
                data = os.read(stdin_fd, 1024)
                if not data:
                    continue
                debug(f"stdin bytes={data!r}")
                if (
                    auto_continue_enabled
                    and time.monotonic() >= disable_allowed_at
                    and should_disable_auto_continue(data)
                ):
                    auto_continue_enabled = False
                    pending_send_bytes.clear()
                    if not disable_notice_shown:
                        os.write(
                            stderr_fd,
                            b"\r\n[codex-auto-continue] disabled for this session.\r\n",
                        )
                        disable_notice_shown = True
                    debug(f"disabled by user input {data!r}")
                os.write(master_fd, data)

            if notify_socket.fileno() in ready:
                payload, _ = notify_socket.recvfrom(65536)
                try:
                    event = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    debug(f"notify payload parse failed: {payload!r}")
                    continue
                debug(f"notify event={event!r}")
                if (
                    auto_continue_enabled
                    and event.get("type") == "agent-turn-complete"
                ):
                    maybe_send_turn_notification(ntfy_config, event)
                    pending_send_bytes = schedule_send(prompt)
                    debug(
                        "received agent-turn-complete; "
                        f"queued auto-continue prompt {prompt!r}"
                    )
    finally:
        signal.signal(signal.SIGWINCH, previous_sigwinch)
        if old_tty_settings is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty_settings)

    _, status = os.waitpid(child_pid, 0)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 1


def drain_queue_nowait(items: queue.Queue) -> list[object]:
    drained = []
    while True:
        try:
            drained.append(items.get_nowait())
        except queue.Empty:
            return drained


def launch_child_windows(args: argparse.Namespace, passthrough: list[str]) -> int:
    if not hasattr(kernel32, "CreatePseudoConsole"):
        raise RuntimeError(
            "Windows auto-continue requires ConPTY "
            "(Windows 10 version 1809 or later)."
        )

    notifier_path = Path(__file__).with_name("codex-auto-continue-notify.py")
    notify_socket, host, port = create_notify_socket()
    notify_socket.settimeout(0.2)

    stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
    stdout_handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    console_state: Optional[dict[str, Optional[int]]] = None
    pty_input_read = wintypes.HANDLE()
    pty_input_write = wintypes.HANDLE()
    pty_output_read = wintypes.HANDLE()
    pty_output_write = wintypes.HANDLE()
    pseudo_console = wintypes.HANDLE()
    process_info = None

    try:
        console_state = configure_windows_console(stdin_handle, stdout_handle)
        create_pipe(pty_input_read, pty_input_write)
        create_pipe(pty_output_read, pty_output_write)
        create_pseudo_console(
            pseudo_console,
            current_console_size(stdout_handle),
            pty_input_read,
            pty_output_write,
        )
        process_info = create_process_in_pseudo_console(
            build_child_argv(
                args.node,
                args.launcher,
                passthrough,
                notifier_path,
                host,
                port,
            ),
            pseudo_console,
        )
        close_handle(pty_input_read)
        close_handle(pty_output_write)

        return forward_loop_windows(
            process_info,
            pseudo_console,
            pty_input_write,
            pty_output_read,
            notify_socket,
            args.prompt,
            args.limit,
            args.ntfy_config,
            stdin_handle,
            stdout_handle,
        )
    finally:
        try:
            notify_socket.close()
        except OSError:
            pass
        if process_info is not None:
            close_handle(process_info.hThread)
            close_handle(process_info.hProcess)
        close_pseudo_console(pseudo_console)
        close_handle(pty_input_read)
        close_handle(pty_input_write)
        close_handle(pty_output_read)
        close_handle(pty_output_write)
        if console_state is not None:
            restore_windows_console(stdin_handle, stdout_handle, console_state)


def configure_windows_console(
    stdin_handle: wintypes.HANDLE, stdout_handle: wintypes.HANDLE
) -> dict[str, Optional[int]]:
    state: dict[str, Optional[int]] = {
        "stdin_mode": get_console_mode(stdin_handle),
        "stdout_mode": get_console_mode(stdout_handle),
        "input_cp": None,
        "output_cp": None,
        "stdout_binary_mode": None,
    }

    if state["stdin_mode"] is not None:
        raw_mode = state["stdin_mode"] | ENABLE_EXTENDED_FLAGS | ENABLE_VIRTUAL_TERMINAL_INPUT
        raw_mode &= ~(
            ENABLE_ECHO_INPUT | ENABLE_LINE_INPUT | ENABLE_PROCESSED_INPUT | ENABLE_QUICK_EDIT_MODE
        )
        set_console_mode(stdin_handle, raw_mode)
        state["input_cp"] = kernel32.GetConsoleCP()
        if not kernel32.SetConsoleCP(CP_UTF8):
            raise ctypes.WinError(ctypes.get_last_error())
        debug("configured Windows stdin for raw VT input")
    else:
        debug("stdin is not a Windows console; disable hotkeys are unavailable")

    if state["stdout_mode"] is not None:
        vt_mode = state["stdout_mode"] | ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        set_console_mode(stdout_handle, vt_mode)
        state["output_cp"] = kernel32.GetConsoleOutputCP()
        if not kernel32.SetConsoleOutputCP(CP_UTF8):
            raise ctypes.WinError(ctypes.get_last_error())
        debug("configured Windows stdout for VT output")

    try:
        state["stdout_binary_mode"] = msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    except OSError:
        state["stdout_binary_mode"] = None

    return state


def restore_windows_console(
    stdin_handle: wintypes.HANDLE,
    stdout_handle: wintypes.HANDLE,
    state: dict[str, Optional[int]],
) -> None:
    try:
        if state.get("stdin_mode") is not None:
            set_console_mode(stdin_handle, int(state["stdin_mode"]))
        if state.get("stdout_mode") is not None:
            set_console_mode(stdout_handle, int(state["stdout_mode"]))
        if state.get("input_cp") is not None:
            kernel32.SetConsoleCP(int(state["input_cp"]))
        if state.get("output_cp") is not None:
            kernel32.SetConsoleOutputCP(int(state["output_cp"]))
        if state.get("stdout_binary_mode") is not None:
            msvcrt.setmode(sys.stdout.fileno(), int(state["stdout_binary_mode"]))
    except OSError:
        pass


def get_console_mode(handle: wintypes.HANDLE) -> Optional[int]:
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return mode.value
    error = ctypes.get_last_error()
    if error == ERROR_INVALID_HANDLE:
        return None
    raise ctypes.WinError(error)


def set_console_mode(handle: wintypes.HANDLE, mode: int) -> None:
    if not kernel32.SetConsoleMode(handle, mode):
        raise ctypes.WinError(ctypes.get_last_error())


def current_console_size(stdout_handle: wintypes.HANDLE) -> tuple[int, int]:
    info = CONSOLE_SCREEN_BUFFER_INFO()
    if not kernel32.GetConsoleScreenBufferInfo(stdout_handle, ctypes.byref(info)):
        return (120, 30)
    width = info.srWindow.Right - info.srWindow.Left + 1
    height = info.srWindow.Bottom - info.srWindow.Top + 1
    return (max(1, width), max(1, height))


def create_pipe(read_handle: wintypes.HANDLE, write_handle: wintypes.HANDLE) -> None:
    if not kernel32.CreatePipe(ctypes.byref(read_handle), ctypes.byref(write_handle), None, 0):
        raise ctypes.WinError(ctypes.get_last_error())


def create_pseudo_console(
    pseudo_console: wintypes.HANDLE,
    size: tuple[int, int],
    input_read: wintypes.HANDLE,
    output_write: wintypes.HANDLE,
) -> None:
    result = kernel32.CreatePseudoConsole(
        COORD(size[0], size[1]),
        input_read,
        output_write,
        0,
        ctypes.byref(pseudo_console),
    )
    if result != 0:
        raise OSError(
            result,
            f"CreatePseudoConsole failed with HRESULT 0x{ctypes.c_ulong(result).value:08X}",
        )


def close_pseudo_console(pseudo_console: wintypes.HANDLE) -> None:
    if pseudo_console and pseudo_console.value:
        kernel32.ClosePseudoConsole(pseudo_console)


def resize_pseudo_console(
    pseudo_console: wintypes.HANDLE, size: tuple[int, int]
) -> None:
    result = kernel32.ResizePseudoConsole(pseudo_console, COORD(size[0], size[1]))
    if result != 0:
        debug(
            "ResizePseudoConsole failed with HRESULT "
            f"0x{ctypes.c_ulong(result).value:08X}"
        )


def create_process_in_pseudo_console(
    child_argv: list[str], pseudo_console: wintypes.HANDLE
) -> PROCESS_INFORMATION:
    attribute_list_size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_list_size))
    attribute_list_buffer = ctypes.create_string_buffer(attribute_list_size.value)
    attribute_list = ctypes.cast(attribute_list_buffer, ctypes.c_void_p)
    if not kernel32.InitializeProcThreadAttributeList(
        attribute_list,
        1,
        0,
        ctypes.byref(attribute_list_size),
    ):
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            pseudo_console,
            ctypes.sizeof(wintypes.HANDLE),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        startup_info = STARTUPINFOEXW()
        startup_info.StartupInfo.cb = ctypes.sizeof(startup_info)
        startup_info.lpAttributeList = attribute_list

        process_info = PROCESS_INFORMATION()
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(child_argv))
        if not kernel32.CreateProcessW(
            None,
            command_line,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT,
            None,
            None,
            ctypes.byref(startup_info),
            ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return process_info
    finally:
        kernel32.DeleteProcThreadAttributeList(attribute_list)


def close_handle(handle: wintypes.HANDLE) -> None:
    if handle and handle.value:
        kernel32.CloseHandle(handle)
        handle.value = None


def write_handle_bytes(handle: wintypes.HANDLE, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        chunk = data[offset:]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not kernel32.WriteFile(
            handle,
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value == 0:
            raise BrokenPipeError("WriteFile returned zero bytes written")
        offset += written.value


def read_handle_bytes(handle: wintypes.HANDLE, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = wintypes.DWORD()
    success = kernel32.ReadFile(handle, buffer, size, ctypes.byref(read), None)
    if not success:
        error = ctypes.get_last_error()
        if error == ERROR_BROKEN_PIPE:
            return b""
        raise ctypes.WinError(error)
    return buffer.raw[: read.value]


def start_windows_stdin_reader(
    stdin_handle: wintypes.HANDLE, stdin_queue: queue.Queue, stop_event: threading.Event
) -> threading.Thread:
    stdin_mode = get_console_mode(stdin_handle)

    def worker() -> None:
        try:
            if stdin_mode is None:
                stdin_fd = sys.stdin.fileno()
                while not stop_event.is_set():
                    data = os.read(stdin_fd, 1024)
                    if not data:
                        break
                    stdin_queue.put(data)
                return

            while not stop_event.is_set():
                data = read_handle_bytes(stdin_handle, 1024)
                if not data:
                    continue
                stdin_queue.put(data)
        except OSError as error:
            debug(f"stdin reader stopped: {error}")

    thread = threading.Thread(target=worker, name="codex-auto-continue-stdin", daemon=True)
    thread.start()
    return thread


def start_windows_output_reader(
    output_read: wintypes.HANDLE, stop_event: threading.Event
) -> threading.Thread:
    stdout_fd = sys.stdout.fileno()

    def worker() -> None:
        try:
            while not stop_event.is_set():
                data = read_handle_bytes(output_read, 65536)
                if not data:
                    return
                os.write(stdout_fd, data)
        except OSError as error:
            debug(f"output reader stopped: {error}")

    thread = threading.Thread(target=worker, name="codex-auto-continue-output", daemon=True)
    thread.start()
    return thread


def start_notify_listener(
    notify_socket: socket.socket,
    notify_queue: queue.Queue,
    stop_event: threading.Event,
) -> threading.Thread:
    def worker() -> None:
        while not stop_event.is_set():
            try:
                payload, _ = notify_socket.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError as error:
                debug(f"notify listener stopped: {error}")
                return

            try:
                event = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                debug(f"notify payload parse failed: {payload!r}")
                continue
            notify_queue.put(event)

    thread = threading.Thread(target=worker, name="codex-auto-continue-notify", daemon=True)
    thread.start()
    return thread


def poll_process_exit(process_handle: wintypes.HANDLE) -> Optional[int]:
    wait_result = kernel32.WaitForSingleObject(process_handle, 0)
    if wait_result == WAIT_TIMEOUT:
        return None
    if wait_result != WAIT_OBJECT_0:
        raise ctypes.WinError(ctypes.get_last_error())
    exit_code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
        raise ctypes.WinError(ctypes.get_last_error())
    return exit_code.value


def wait_for_process_exit(process_handle: wintypes.HANDLE) -> int:
    wait_result = kernel32.WaitForSingleObject(process_handle, 0xFFFFFFFF)
    if wait_result != WAIT_OBJECT_0:
        raise ctypes.WinError(ctypes.get_last_error())
    exit_code = wintypes.DWORD()
    if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
        raise ctypes.WinError(ctypes.get_last_error())
    return exit_code.value


def forward_loop_windows(
    process_info: PROCESS_INFORMATION,
    pseudo_console: wintypes.HANDLE,
    input_write: wintypes.HANDLE,
    output_read: wintypes.HANDLE,
    notify_socket: socket.socket,
    prompt: str,
    limit: Optional[int],
    ntfy_config: Optional[NtfyConfig],
    stdin_handle: wintypes.HANDLE,
    stdout_handle: wintypes.HANDLE,
) -> int:
    auto_continue_enabled = True
    remaining_sends = limit
    pending_send_bytes: list[tuple[float, bytes]] = []
    disable_allowed_at = time.monotonic() + DISABLE_GUARD_SECONDS
    disable_notice_shown = False
    limit_notice_shown = False
    stop_event = threading.Event()
    stdin_queue: queue.Queue = queue.Queue()
    notify_queue: queue.Queue = queue.Queue()
    stdin_thread = start_windows_stdin_reader(stdin_handle, stdin_queue, stop_event)
    output_thread = start_windows_output_reader(output_read, stop_event)
    notify_thread = start_notify_listener(notify_socket, notify_queue, stop_event)
    _ = stdin_thread, notify_thread
    last_console_size = current_console_size(stdout_handle)
    last_resize_check = 0.0
    exit_code: Optional[int] = None

    try:
        while True:
            now = time.monotonic()
            if pending_send_bytes and now >= pending_send_bytes[0][0]:
                _, chunk = pending_send_bytes.pop(0)
                try:
                    write_handle_bytes(input_write, chunk)
                except OSError:
                    break
                if chunk == QUEUE_KEY:
                    debug("sent queue key")
                    if remaining_sends is not None:
                        remaining_sends -= 1
                        debug(f"remaining auto-continue sends: {remaining_sends}")
                        if remaining_sends == 0:
                            auto_continue_enabled = False
                            if not limit_notice_shown:
                                os.write(
                                    sys.stderr.fileno(),
                                    b"\r\n[codex-auto-continue] limit reached; disabled for this session.\r\n",
                                )
                                limit_notice_shown = True
                            debug("auto-continue limit reached")
                else:
                    debug(f"sent text chunk {chunk!r}")
                continue

            stop_loop = False
            for data in drain_queue_nowait(stdin_queue):
                assert isinstance(data, bytes)
                debug(f"stdin bytes={data!r}")
                if (
                    auto_continue_enabled
                    and now >= disable_allowed_at
                    and should_disable_auto_continue(data)
                ):
                    auto_continue_enabled = False
                    pending_send_bytes.clear()
                    if not disable_notice_shown:
                        os.write(
                            sys.stderr.fileno(),
                            b"\r\n[codex-auto-continue] disabled for this session.\r\n",
                        )
                        disable_notice_shown = True
                    debug(f"disabled by user input {data!r}")
                try:
                    write_handle_bytes(input_write, data)
                except OSError:
                    stop_loop = True
                    break
            if stop_loop:
                break

            for event in drain_queue_nowait(notify_queue):
                assert isinstance(event, dict)
                debug(f"notify event={event!r}")
                if auto_continue_enabled and event.get("type") == "agent-turn-complete":
                    maybe_send_turn_notification(ntfy_config, event)
                    pending_send_bytes = schedule_send(prompt)
                    debug(
                        "received agent-turn-complete; "
                        f"queued auto-continue prompt {prompt!r}"
                    )

            if now - last_resize_check >= 0.2:
                current_size = current_console_size(stdout_handle)
                if current_size != last_console_size:
                    resize_pseudo_console(pseudo_console, current_size)
                    last_console_size = current_size
                last_resize_check = now

            polled_exit = poll_process_exit(process_info.hProcess)
            if polled_exit is not None:
                exit_code = polled_exit
                if not output_thread.is_alive():
                    break

            sleep_for = 0.02
            if pending_send_bytes:
                sleep_for = min(
                    sleep_for,
                    max(0.0, pending_send_bytes[0][0] - time.monotonic()),
                )
            time.sleep(max(0.0, sleep_for))
    finally:
        stop_event.set()
        close_handle(input_write)
        try:
            notify_socket.close()
        except OSError:
            pass
        output_thread.join(timeout=1.0)

    return exit_code if exit_code is not None else wait_for_process_exit(process_info.hProcess)


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--limit", type=parse_positive_int)
    parser.add_argument("--ntfy-topic")
    parser.add_argument("--ntfy-base-url")
    parser.add_argument("--notify-timeout-ms", type=parse_positive_int)
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    passthrough = list(args.passthrough)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    return args, passthrough


def main() -> int:
    try:
        args, passthrough = parse_args()
        args.ntfy_config = build_ntfy_config(args)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if os.name == "nt":
        return launch_child_windows(args, passthrough)
    return launch_child_unix(args, passthrough)


if __name__ == "__main__":
    raise SystemExit(main())
