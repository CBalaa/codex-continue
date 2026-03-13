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
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

AUTO_SEND_DELAY_SECONDS = 0.2
CHAR_SEND_INTERVAL_SECONDS = 0.03
CONTROL_POLL_INTERVAL_SECONDS = 0.1
CONTROL_RECONNECT_DELAY_SECONDS = 1.0
CONTROL_STREAM_TIMEOUT_SECONDS = 90.0
ESC_HOTKEY_DISAMBIGUATION_SECONDS = 0.1
QUEUE_KEY = b"\t"
DISABLE_GUARD_SECONDS = 1.0
CTRL_C = 3
ESC = 27
LOCALHOST = "127.0.0.1"
DEFAULT_NTFY_BASE_URL = "https://ntfy.sh"
DEFAULT_NOTIFY_TIMEOUT_MS = 3000
MANUAL_MODE = "manual"
AUTO_MODE = "auto"
CHAT_MODE = "chat"
AUTO_SOURCE = "auto"
CHAT_SOURCE = "chat"
USER_SENDER = "user"
CODEX_SENDER = "codex"
NTFY_TURN_NOTIFICATION_TITLE = "Codex turn complete"
NTFY_CONTROL_NOTIFICATION_TITLE = "Codex remote control"
NTFY_CONTROL_ERROR_TITLE = "Codex remote control error"

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


@dataclass
class AutoTask:
    message: str
    remaining: Optional[int]


@dataclass(frozen=True)
class ScheduledSend:
    message: str
    source: str


@dataclass(frozen=True)
class RemoteCommand:
    kind: str
    auto_tasks: tuple[AutoTask, ...] = ()
    chat_messages: tuple[str, ...] = ()


@dataclass
class SessionState:
    mode: str
    auto_tasks: deque[AutoTask]
    chat_queue: deque[str]
    turn_in_flight: bool = False


@dataclass
class StdinHotkeyState:
    pending_escape: bytes = b""
    pending_escape_deadline: Optional[float] = None


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


def append_text_block(lines: list[str], label: str, text: str) -> None:
    lines.append(f"{label}:")
    lines.extend(text.split("\n"))


def format_remaining_count(value: Optional[int]) -> str:
    if value is None:
        return "unlimited"
    return str(value)


def total_auto_remaining(auto_tasks: deque[AutoTask]) -> Optional[int]:
    total = 0
    for task in auto_tasks:
        if task.remaining is None:
            return None
        total += task.remaining
    return total


def current_auto_task(state: SessionState) -> Optional[AutoTask]:
    if not state.auto_tasks:
        return None
    return state.auto_tasks[0]


def build_initial_session_state(
    mode: str, prompt: str, limit: Optional[int]
) -> SessionState:
    if mode == CHAT_MODE:
        return SessionState(
            mode=CHAT_MODE,
            auto_tasks=deque(),
            chat_queue=deque(),
        )
    return SessionState(
        mode=AUTO_MODE,
        auto_tasks=deque([AutoTask(message=prompt, remaining=limit)]),
        chat_queue=deque(),
    )


def build_state_payload(state: SessionState) -> dict[str, object]:
    payload: dict[str, object] = {"mode": state.mode}
    if state.mode == AUTO_MODE:
        payload["remaining_total"] = total_auto_remaining(state.auto_tasks)
        task = current_auto_task(state)
        if task is not None:
            payload["current_task_remaining"] = task.remaining
            payload["current_task_message"] = task.message
    elif state.mode == CHAT_MODE:
        payload["queued_chat_messages"] = len(state.chat_queue)
    return payload


def summarize_state_lines(state: SessionState) -> list[str]:
    lines = [f"mode: {state.mode}"]
    if state.mode == AUTO_MODE:
        lines.append(
            f"remaining-total: {format_remaining_count(total_auto_remaining(state.auto_tasks))}"
        )
        task = current_auto_task(state)
        if task is not None:
            lines.append(
                f"current-task-remaining: {format_remaining_count(task.remaining)}"
            )
            append_text_block(lines, "current-task-message", task.message)
    elif state.mode == CHAT_MODE:
        lines.append(f"queued-chat-messages: {len(state.chat_queue)}")
    return lines


def format_turn_notification(event: dict[str, object], state: SessionState) -> str:
    lines: list[str] = []
    lines.extend(summarize_state_lines(state))

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
    append_text_block(lines, "assistant", last_assistant_message(event))
    return "\n".join(lines)


def build_turn_notification_payload(
    event: dict[str, object], state: SessionState
) -> dict[str, object]:
    payload: dict[str, object] = {
        "sender": CODEX_SENDER,
        "type": "turn-complete",
        "text": format_turn_notification(event, state),
        **build_state_payload(state),
    }

    cwd = coerce_notification_text(event.get("cwd"))
    if cwd is not None:
        payload["cwd"] = cwd

    user_message = last_input_message(event)
    if user_message is not None:
        payload["user"] = user_message

    payload["assistant"] = last_assistant_message(event)

    thread_id = coerce_notification_text(event.get("thread-id"))
    if thread_id is not None:
        payload["thread_id"] = thread_id

    turn_id = coerce_notification_text(event.get("turn-id"))
    if turn_id is not None:
        payload["turn_id"] = turn_id

    return payload


def format_control_response(
    state: SessionState,
    command_summary: str,
    status: str,
    detail: Optional[str] = None,
) -> str:
    lines = [f"command: {command_summary}", f"status: {status}"]
    if detail is not None:
        lines.append(f"detail: {detail}")
    lines.extend(summarize_state_lines(state))
    return "\n".join(lines)


def build_control_response_payload(
    state: SessionState, body: str, error: bool = False
) -> dict[str, object]:
    return {
        "sender": CODEX_SENDER,
        "type": "control-error" if error else "control-response",
        "text": body,
        **build_state_payload(state),
    }


def build_ntfy_topic_url(base_url: str, topic: str) -> str:
    return f"{base_url}/{urllib_parse.quote(topic, safe='')}"


def build_ntfy_stream_url(base_url: str, topic: str, since: str) -> str:
    query = urllib_parse.urlencode({"since": since})
    return f"{build_ntfy_topic_url(base_url, topic)}/json?{query}"


def serialize_ntfy_body(body: object) -> tuple[bytes, str]:
    if isinstance(body, dict):
        return (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            "application/json; charset=utf-8",
        )
    if isinstance(body, str):
        return body.encode("utf-8"), "text/plain; charset=utf-8"
    raise TypeError("unsupported ntfy body type")


def send_ntfy_message(config: NtfyConfig, title: str, body: object) -> None:
    payload_bytes, content_type = serialize_ntfy_body(body)
    request = urllib_request.Request(
        build_ntfy_topic_url(config.base_url, config.topic),
        data=payload_bytes,
        headers={
            "Content-Type": content_type,
            "Title": title,
        },
        method="POST",
    )
    with urllib_request.urlopen(request, timeout=config.timeout_seconds) as response:
        response.read()


def maybe_send_ntfy_message(
    config: Optional[NtfyConfig], title: str, body: object, context: str
) -> None:
    if config is None:
        return

    try:
        send_ntfy_message(config, title, body)
        debug(
            f"sent ntfy {context} notification "
            f"for topic {config.topic!r}"
        )
    except (OSError, ValueError, urllib_error.URLError) as error:
        debug(f"ntfy {context} notification failed: {error}")


def maybe_send_turn_notification(
    config: Optional[NtfyConfig], event: dict[str, object], state: SessionState
) -> None:
    maybe_send_ntfy_message(
        config,
        NTFY_TURN_NOTIFICATION_TITLE,
        build_turn_notification_payload(event, state),
        "turn",
    )


def maybe_send_control_response(
    config: Optional[NtfyConfig],
    state: SessionState,
    body: str,
    error: bool = False,
) -> None:
    maybe_send_ntfy_message(
        config,
        NTFY_CONTROL_ERROR_TITLE if error else NTFY_CONTROL_NOTIFICATION_TITLE,
        build_control_response_payload(state, body, error),
        "control",
    )


def is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def parse_auto_tasks(payload: object) -> tuple[AutoTask, ...]:
    if not isinstance(payload, list) or not payload:
        raise ValueError('"tasks" must be a non-empty array')

    parsed_tasks: list[AutoTask] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError('"tasks" entries must be objects')

        message = coerce_notification_text(item.get("message"))
        if message is None:
            raise ValueError('"tasks[].message" must be a non-empty string')

        count = item.get("count")
        if not is_positive_int(count):
            raise ValueError('"tasks[].count" must be a positive integer')

        parsed_tasks.append(AutoTask(message=message, remaining=int(count)))
    return tuple(parsed_tasks)


def parse_chat_messages(payload: object) -> tuple[str, ...]:
    if not isinstance(payload, list) or not payload:
        raise ValueError('"messages" must be a non-empty array')

    parsed_messages: list[str] = []
    for item in payload:
        message = coerce_notification_text(item)
        if message is None:
            raise ValueError('"messages" entries must be non-empty strings')
        parsed_messages.append(message)
    return tuple(parsed_messages)


def parse_remote_command_payload(payload: dict[str, object]) -> RemoteCommand:
    command = payload.get("command")
    mode = payload.get("mode")
    if command is not None and mode is not None:
        raise ValueError('control message must use either "command" or "mode"')

    if command is not None:
        if command != "stop_auto":
            raise ValueError('unsupported command; expected "stop_auto"')
        return RemoteCommand(kind="stop_auto")

    if mode == AUTO_MODE:
        return RemoteCommand(kind=AUTO_MODE, auto_tasks=parse_auto_tasks(payload.get("tasks")))
    if mode == CHAT_MODE:
        return RemoteCommand(
            kind=CHAT_MODE,
            chat_messages=parse_chat_messages(payload.get("messages")),
        )
    raise ValueError('unsupported mode; expected "auto" or "chat"')


def parse_remote_command_message(message: str) -> Optional[RemoteCommand]:
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as error:
        raise ValueError("control message body must be valid JSON") from error

    if not isinstance(payload, dict):
        raise ValueError("control message body must be a JSON object")

    sender = payload.get("sender")
    if sender == CODEX_SENDER:
        return None
    if sender != USER_SENDER:
        raise ValueError('control messages must include "sender":"user"')

    return parse_remote_command_payload(payload)


def maybe_next_scheduled_send(
    state: SessionState, current_send: Optional[ScheduledSend]
) -> Optional[ScheduledSend]:
    if current_send is not None or state.turn_in_flight:
        return None

    if state.mode == AUTO_MODE:
        task = current_auto_task(state)
        if task is None:
            return None
        return ScheduledSend(message=task.message, source=AUTO_SOURCE)

    if state.mode == CHAT_MODE and state.chat_queue:
        return ScheduledSend(message=state.chat_queue[0], source=CHAT_SOURCE)

    return None


def commit_scheduled_send(state: SessionState, scheduled: ScheduledSend) -> None:
    state.turn_in_flight = True
    if scheduled.source == AUTO_SOURCE:
        task = current_auto_task(state)
        if task is None:
            return
        if task.remaining is not None:
            task.remaining -= 1
            if task.remaining <= 0:
                state.auto_tasks.popleft()
        return

    if scheduled.source == CHAT_SOURCE and state.chat_queue:
        state.chat_queue.popleft()


def maybe_finalize_idle_mode(
    state: SessionState, config: Optional[NtfyConfig]
) -> None:
    if state.turn_in_flight:
        return

    if state.mode == AUTO_MODE and not state.auto_tasks:
        state.mode = MANUAL_MODE
        maybe_send_control_response(
            config,
            state,
            format_control_response(
                state,
                "auto-queue",
                "completed",
                "auto queue finished; switched to manual mode",
            ),
        )


def apply_remote_command(state: SessionState, command: RemoteCommand) -> str:
    if command.kind == AUTO_MODE:
        state.mode = AUTO_MODE
        state.auto_tasks = deque(
            AutoTask(message=task.message, remaining=task.remaining)
            for task in command.auto_tasks
        )
        state.chat_queue.clear()
        return format_control_response(
            state,
            "auto",
            "applied",
            f"loaded {len(command.auto_tasks)} auto task(s)",
        )

    if command.kind == CHAT_MODE:
        state.mode = CHAT_MODE
        state.auto_tasks.clear()
        state.chat_queue.extend(command.chat_messages)
        return format_control_response(
            state,
            "chat",
            "applied",
            f"queued {len(command.chat_messages)} chat message(s)",
        )

    if state.mode != AUTO_MODE:
        return format_control_response(
            state,
            "stop_auto",
            "ignored",
            "stop_auto only applies while mode=auto",
        )

    state.mode = MANUAL_MODE
    state.auto_tasks.clear()
    return format_control_response(
        state,
        "stop_auto",
        "applied",
        "switched to manual mode and cleared auto tasks",
    )


def build_control_message_summary(event: dict[str, object]) -> str:
    message_id = coerce_notification_text(event.get("id"))
    if message_id is None:
        return "remote"
    return f"remote:{message_id}"


def should_cancel_pending_send(
    command: RemoteCommand, current_send: Optional[ScheduledSend], turn_in_flight: bool
) -> bool:
    if turn_in_flight or current_send is None:
        return False
    if command.kind == AUTO_MODE:
        return True
    if command.kind == CHAT_MODE:
        return current_send.source == AUTO_SOURCE
    return current_send.source == AUTO_SOURCE


def process_control_message(
    state: SessionState,
    config: Optional[NtfyConfig],
    event: dict[str, object],
    current_send: Optional[ScheduledSend],
) -> bool:
    raw_message = coerce_notification_text(event.get("message"))
    if raw_message is None:
        maybe_send_control_response(
            config,
            state,
            format_control_response(
                state,
                build_control_message_summary(event),
                "error",
                "control message body must be a non-empty string",
            ),
            error=True,
        )
        return False

    try:
        command = parse_remote_command_message(raw_message)
    except ValueError as error:
        maybe_send_control_response(
            config,
            state,
            format_control_response(
                state,
                build_control_message_summary(event),
                "error",
                str(error),
            ),
            error=True,
        )
        return False

    if command is None:
        return False

    cancel_pending = should_cancel_pending_send(command, current_send, state.turn_in_flight)
    maybe_send_control_response(config, state, apply_remote_command(state, command))
    return cancel_pending


def start_ntfy_control_listener(
    config: Optional[NtfyConfig],
    control_queue: queue.Queue,
    stop_event: threading.Event,
) -> Optional[threading.Thread]:
    if config is None:
        return None

    def worker() -> None:
        since = str(int(time.time()))
        last_message_id: Optional[str] = None

        while not stop_event.is_set():
            request = urllib_request.Request(
                build_ntfy_stream_url(config.base_url, config.topic, since),
                method="GET",
            )
            try:
                with urllib_request.urlopen(
                    request,
                    timeout=max(config.timeout_seconds, CONTROL_STREAM_TIMEOUT_SECONDS),
                ) as response:
                    for raw_line in response:
                        if stop_event.is_set():
                            return

                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue

                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            debug(f"control stream payload parse failed: {line!r}")
                            continue

                        if not isinstance(event, dict):
                            continue
                        if event.get("event") != "message":
                            continue

                        message_id = event.get("id")
                        if isinstance(message_id, str) and message_id:
                            if message_id == last_message_id:
                                continue
                            last_message_id = message_id
                            since = message_id

                        control_queue.put(event)
            except (
                OSError,
                UnicodeDecodeError,
                ValueError,
                urllib_error.URLError,
            ) as error:
                debug(f"control listener reconnecting after error: {error}")
                stop_event.wait(CONTROL_RECONNECT_DELAY_SECONDS)

    thread = threading.Thread(target=worker, name="codex-auto-continue-control", daemon=True)
    thread.start()
    return thread


def flush_pending_stdin_hotkey(
    state: StdinHotkeyState, now: float
) -> tuple[bytes, bool]:
    if (
        state.pending_escape
        and state.pending_escape_deadline is not None
        and now >= state.pending_escape_deadline
    ):
        state.pending_escape = b""
        state.pending_escape_deadline = None
        debug("resolved buffered stdin Esc as bare Esc hotkey")
        return b"\x1b", True

    return b"", False


def process_stdin_hotkeys(
    state: StdinHotkeyState, data: bytes, now: float
) -> tuple[bytes, bool]:
    combined = state.pending_escape + data
    if state.pending_escape:
        debug(f"resuming buffered stdin Esc with bytes={data!r}")
    state.pending_escape = b""
    state.pending_escape_deadline = None

    forwarded = bytearray()
    index = 0

    while index < len(combined):
        value = combined[index]
        if value == CTRL_C:
            forwarded.extend(combined[index:])
            return bytes(forwarded), True

        if value != ESC:
            forwarded.append(value)
            index += 1
            continue

        if index + 1 >= len(combined):
            state.pending_escape = b"\x1b"
            state.pending_escape_deadline = now + ESC_HOTKEY_DISAMBIGUATION_SECONDS
            debug("buffered trailing stdin Esc awaiting sequence continuation")
            break

        forwarded.append(value)
        index += 1

    return bytes(forwarded), False


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


def schedule_submission(
    state: SessionState, current_send: Optional[ScheduledSend]
) -> tuple[Optional[ScheduledSend], list[tuple[float, bytes]]]:
    scheduled = maybe_next_scheduled_send(state, current_send)
    if scheduled is None:
        return current_send, []
    debug(f"scheduled {scheduled.source} submission {scheduled.message!r}")
    return scheduled, schedule_send(scheduled.message)


def disable_session_automation(
    state: SessionState,
) -> None:
    state.mode = MANUAL_MODE
    state.auto_tasks.clear()
    state.chat_queue.clear()


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
            args.mode,
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
    mode: str,
    prompt: str,
    limit: Optional[int],
    ntfy_config: Optional[NtfyConfig],
) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stderr_fd = sys.stderr.fileno()
    stdin_is_tty = os.isatty(stdin_fd)
    old_tty_settings = None
    session_state = build_initial_session_state(mode, prompt, limit)
    pending_send_bytes: list[tuple[float, bytes]] = []
    pending_submission: Optional[ScheduledSend] = None
    stdin_hotkey_state = StdinHotkeyState()
    disable_allowed_at = time.monotonic() + DISABLE_GUARD_SECONDS
    stop_event = threading.Event()
    control_queue: queue.Queue = queue.Queue()
    control_thread = start_ntfy_control_listener(ntfy_config, control_queue, stop_event)

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

    try:
        while True:
            now = time.monotonic()
            if stdin_is_tty:
                hotkey_bytes, disable_candidate = flush_pending_stdin_hotkey(
                    stdin_hotkey_state, now
                )
                if hotkey_bytes:
                    debug(f"stdin hotkey flush bytes={hotkey_bytes!r}")
                    if disable_candidate and now >= disable_allowed_at:
                        disable_session_automation(session_state)
                        pending_send_bytes.clear()
                        pending_submission = None
                        if not disable_notice_shown:
                            os.write(
                                stderr_fd,
                                b"\r\n[codex-auto-continue] switched to manual mode.\r\n",
                            )
                            disable_notice_shown = True
                        debug(f"disabled by buffered stdin hotkey {hotkey_bytes!r}")
                    os.write(master_fd, hotkey_bytes)

            for control_event in drain_queue_nowait(control_queue):
                assert isinstance(control_event, dict)
                debug(f"control event={control_event!r}")
                if process_control_message(
                    session_state,
                    ntfy_config,
                    control_event,
                    pending_submission,
                ):
                    pending_send_bytes.clear()
                    pending_submission = None
                    debug("cleared pending scheduled send after control update")

            maybe_finalize_idle_mode(session_state, ntfy_config)
            pending_submission, new_send_bytes = schedule_submission(
                session_state, pending_submission
            )
            if new_send_bytes:
                pending_send_bytes = new_send_bytes

            if pending_send_bytes and now >= pending_send_bytes[0][0]:
                _, chunk = pending_send_bytes.pop(0)
                os.write(master_fd, chunk)
                if chunk == QUEUE_KEY:
                    debug("sent queue key")
                    if pending_submission is not None:
                        debug(
                            "committing scheduled submission "
                            f"{pending_submission.source}:{pending_submission.message!r}"
                        )
                        commit_scheduled_send(session_state, pending_submission)
                        pending_submission = None
                else:
                    debug(f"sent text chunk {chunk!r}")

            timeout = None
            if pending_send_bytes:
                timeout = max(0.0, pending_send_bytes[0][0] - now)
            if (
                stdin_is_tty
                and stdin_hotkey_state.pending_escape_deadline is not None
            ):
                hotkey_timeout = max(
                    0.0, stdin_hotkey_state.pending_escape_deadline - now
                )
                timeout = (
                    hotkey_timeout
                    if timeout is None
                    else min(timeout, hotkey_timeout)
                )
            if control_thread is not None:
                timeout = (
                    CONTROL_POLL_INTERVAL_SECONDS
                    if timeout is None
                    else min(timeout, CONTROL_POLL_INTERVAL_SECONDS)
                )

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
                data, disable_candidate = process_stdin_hotkeys(
                    stdin_hotkey_state,
                    data,
                    time.monotonic(),
                )
                if not data and not disable_candidate:
                    continue
                helper_send_in_progress = bool(pending_send_bytes)
                if (
                    disable_candidate
                    and time.monotonic() >= disable_allowed_at
                ):
                    disable_session_automation(session_state)
                    pending_send_bytes.clear()
                    pending_submission = None
                    helper_send_in_progress = False
                    if not disable_notice_shown:
                        os.write(
                            stderr_fd,
                            b"\r\n[codex-auto-continue] switched to manual mode.\r\n",
                        )
                        disable_notice_shown = True
                    debug(f"disabled by user input {data!r}")
                if helper_send_in_progress:
                    debug(f"suppressed stdin during scheduled send: {data!r}")
                    continue
                os.write(master_fd, data)

            if notify_socket.fileno() in ready:
                payload, _ = notify_socket.recvfrom(65536)
                try:
                    event = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    debug(f"notify payload parse failed: {payload!r}")
                    continue
                debug(f"notify event={event!r}")
                if event.get("type") == "agent-turn-complete":
                    session_state.turn_in_flight = False
                    maybe_send_turn_notification(ntfy_config, event, session_state)
                    maybe_finalize_idle_mode(session_state, ntfy_config)
    finally:
        stop_event.set()
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
            args.mode,
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
    mode: str,
    prompt: str,
    limit: Optional[int],
    ntfy_config: Optional[NtfyConfig],
    stdin_handle: wintypes.HANDLE,
    stdout_handle: wintypes.HANDLE,
) -> int:
    session_state = build_initial_session_state(mode, prompt, limit)
    pending_send_bytes: list[tuple[float, bytes]] = []
    pending_submission: Optional[ScheduledSend] = None
    stdin_hotkey_state = StdinHotkeyState()
    disable_allowed_at = time.monotonic() + DISABLE_GUARD_SECONDS
    disable_notice_shown = False
    stop_event = threading.Event()
    stdin_queue: queue.Queue = queue.Queue()
    notify_queue: queue.Queue = queue.Queue()
    control_queue: queue.Queue = queue.Queue()
    stdin_thread = start_windows_stdin_reader(stdin_handle, stdin_queue, stop_event)
    output_thread = start_windows_output_reader(output_read, stop_event)
    notify_thread = start_notify_listener(notify_socket, notify_queue, stop_event)
    control_thread = start_ntfy_control_listener(ntfy_config, control_queue, stop_event)
    _ = stdin_thread, notify_thread, control_thread
    last_console_size = current_console_size(stdout_handle)
    last_resize_check = 0.0
    exit_code: Optional[int] = None

    try:
        while True:
            now = time.monotonic()
            hotkey_bytes, disable_candidate = flush_pending_stdin_hotkey(
                stdin_hotkey_state, now
            )
            if hotkey_bytes:
                debug(f"stdin hotkey flush bytes={hotkey_bytes!r}")
                if disable_candidate and now >= disable_allowed_at:
                    disable_session_automation(session_state)
                    pending_send_bytes.clear()
                    pending_submission = None
                    if not disable_notice_shown:
                        os.write(
                            sys.stderr.fileno(),
                            b"\r\n[codex-auto-continue] switched to manual mode.\r\n",
                        )
                        disable_notice_shown = True
                    debug(f"disabled by buffered stdin hotkey {hotkey_bytes!r}")
                try:
                    write_handle_bytes(input_write, hotkey_bytes)
                except OSError:
                    break

            for control_event in drain_queue_nowait(control_queue):
                assert isinstance(control_event, dict)
                debug(f"control event={control_event!r}")
                if process_control_message(
                    session_state,
                    ntfy_config,
                    control_event,
                    pending_submission,
                ):
                    pending_send_bytes.clear()
                    pending_submission = None
                    debug("cleared pending scheduled send after control update")

            maybe_finalize_idle_mode(session_state, ntfy_config)
            pending_submission, new_send_bytes = schedule_submission(
                session_state, pending_submission
            )
            if new_send_bytes:
                pending_send_bytes = new_send_bytes

            if pending_send_bytes and now >= pending_send_bytes[0][0]:
                _, chunk = pending_send_bytes.pop(0)
                try:
                    write_handle_bytes(input_write, chunk)
                except OSError:
                    break
                if chunk == QUEUE_KEY:
                    debug("sent queue key")
                    if pending_submission is not None:
                        debug(
                            "committing scheduled submission "
                            f"{pending_submission.source}:{pending_submission.message!r}"
                        )
                        commit_scheduled_send(session_state, pending_submission)
                        pending_submission = None
                else:
                    debug(f"sent text chunk {chunk!r}")
                continue

            stop_loop = False
            for data in drain_queue_nowait(stdin_queue):
                assert isinstance(data, bytes)
                debug(f"stdin bytes={data!r}")
                data, disable_candidate = process_stdin_hotkeys(
                    stdin_hotkey_state,
                    data,
                    time.monotonic(),
                )
                if not data and not disable_candidate:
                    continue
                helper_send_in_progress = bool(pending_send_bytes)
                if disable_candidate and now >= disable_allowed_at:
                    disable_session_automation(session_state)
                    pending_send_bytes.clear()
                    pending_submission = None
                    helper_send_in_progress = False
                    if not disable_notice_shown:
                        os.write(
                            sys.stderr.fileno(),
                            b"\r\n[codex-auto-continue] switched to manual mode.\r\n",
                        )
                        disable_notice_shown = True
                    debug(f"disabled by user input {data!r}")
                if helper_send_in_progress:
                    debug(f"suppressed stdin during scheduled send: {data!r}")
                    continue
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
                if event.get("type") == "agent-turn-complete":
                    session_state.turn_in_flight = False
                    maybe_send_turn_notification(ntfy_config, event, session_state)
                    maybe_finalize_idle_mode(session_state, ntfy_config)

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
            if stdin_hotkey_state.pending_escape_deadline is not None:
                sleep_for = min(
                    sleep_for,
                    max(
                        0.0,
                        stdin_hotkey_state.pending_escape_deadline
                        - time.monotonic(),
                    ),
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
    parser.add_argument("--mode", choices=[AUTO_MODE, CHAT_MODE], default=AUTO_MODE)
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
        if args.mode == CHAT_MODE and args.ntfy_config is None:
            raise ValueError("--mode chat requires --ntfy-topic")
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if os.name == "nt":
        return launch_child_windows(args, passthrough)
    return launch_child_unix(args, passthrough)


if __name__ == "__main__":
    raise SystemExit(main())
