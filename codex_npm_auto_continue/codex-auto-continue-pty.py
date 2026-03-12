#!/usr/bin/env python3

import argparse
import fcntl
import json
import os
import pty
import select
import shutil
import signal
import socket
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path

AUTO_SEND_DELAY_SECONDS = 0.2
CHAR_SEND_INTERVAL_SECONDS = 0.03
QUEUE_KEY = b"\t"
DISABLE_GUARD_SECONDS = 1.0
CTRL_C = 3
ESC = 27


def build_notify_override(python_bin: str, notifier_path: Path, socket_path: Path) -> str:
    notify_argv = [python_bin, str(notifier_path), str(socket_path)]
    return f"notify={json.dumps(notify_argv)}"


def copy_winsize(source_fd: int, target_fd: int) -> None:
    try:
        packed = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(target_fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


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
        "/tmp/codex-auto-continue-debug.log",
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
    os.write(sys.stderr.fileno(), f"\r\n{line}\r\n".encode("utf-8"))


def schedule_send(prompt: str) -> list[tuple[float, bytes]]:
    queued: list[tuple[float, bytes]] = []
    send_at = time.monotonic() + AUTO_SEND_DELAY_SECONDS
    for ch in prompt:
        queued.append((send_at, ch.encode("utf-8")))
        send_at += CHAR_SEND_INTERVAL_SECONDS
    queued.append((send_at, QUEUE_KEY))
    return queued


def forward_loop(
    child_pid: int, master_fd: int, notify_socket: socket.socket, prompt: str
) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stderr_fd = sys.stderr.fileno()
    stdin_is_tty = os.isatty(stdin_fd)
    old_tty_settings = None
    auto_continue_enabled = True
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
        copy_winsize(stdin_fd, master_fd)
    else:
        debug("stdin is not a tty; disable hotkeys are unavailable")

    def on_sigwinch(_signum, _frame):
        if stdin_is_tty:
            copy_winsize(stdin_fd, master_fd)

    previous_sigwinch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, on_sigwinch)

    disable_notice_shown = False

    try:
        while True:
            if pending_send_bytes and time.monotonic() >= pending_send_bytes[0][0]:
                _, chunk = pending_send_bytes.pop(0)
                os.write(master_fd, chunk)
                if chunk == QUEUE_KEY:
                    debug("sent queue key")
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


def launch_child(args: argparse.Namespace, passthrough: list[str]) -> int:
    runtime_dir = Path(tempfile.mkdtemp(prefix="codex-auto-continue-"))
    socket_path = runtime_dir / "notify.sock"
    notifier_path = Path(__file__).with_name("codex-auto-continue-notify.py")
    python_bin = shutil.which("python3") or shutil.which("python") or sys.executable

    notify_socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    notify_socket.bind(str(socket_path))
    notify_socket.setblocking(False)

    child_env = os.environ.copy()
    child_argv = [
        args.node,
        args.launcher,
        "--config",
        build_notify_override(python_bin, notifier_path, socket_path),
        *passthrough,
    ]

    child_pid, master_fd = pty.fork()
    if child_pid == 0:
        os.execvpe(args.node, child_argv, child_env)

    try:
        return forward_loop(child_pid, master_fd, notify_socket, args.prompt)
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        notify_socket.close()
        shutil.rmtree(runtime_dir, ignore_errors=True)


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    passthrough = list(args.passthrough)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    return args, passthrough


def main() -> int:
    args, passthrough = parse_args()
    return launch_child(args, passthrough)

if __name__ == "__main__":
    raise SystemExit(main())
