#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import json
import os
import pty
import queue
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

import fcntl
import select
import signal
import termios
import tty

AUTO_SEND_DELAY_SECONDS = 0.2
CHAR_SEND_INTERVAL_SECONDS = 0.03
CONTROL_POLL_INTERVAL_SECONDS = 0.1
ESC_HOTKEY_DISAMBIGUATION_SECONDS = 0.1
DISABLE_GUARD_SECONDS = 1.0
SSE_KEEPALIVE_SECONDS = 15.0
MAX_RECENT_EVENTS = 40
MAX_REQUEST_BODY_BYTES = 65536
MAX_SSE_QUEUE_SIZE = 32
SERVER_START_TIMEOUT_SECONDS = 5.0
SERVER_COMMAND_POLL_TIMEOUT_SECONDS = 15.0
SERVER_REQUEST_TIMEOUT_SECONDS = 5.0
QUEUE_KEY = b"\t"
CTRL_C = 3
ESC = 27
LOCALHOST = "127.0.0.1"
MANUAL_MODE = "manual"
AUTO_MODE = "auto"
CHAT_MODE = "chat"
AUTO_SOURCE = "auto"
CHAT_SOURCE = "chat"
LOCAL_SOURCE = "local"
USER_SENDER = "user"
CODEX_SENDER = "codex"
SESSION_COOKIE_NAME = "codex_remote_session"
STATIC_HTML = "codex-auto-continue-web.html"
STATIC_CSS = "codex-auto-continue-web.css"
STATIC_JS = "codex-auto-continue-web.js"
SERVER_SCRIPT = "codex-auto-continue-web-server.py"


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


@dataclass(frozen=True)
class QueuedControlCommand:
    summary: str
    command: RemoteCommand


class RemoteConsoleClient:
    def __init__(
        self,
        bind: str,
        port: int,
        password: str,
        control_key: str,
        control_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        self.bind = bind
        self.port = port
        self.password = password
        self.control_key = control_key
        self.control_key_hint = mask_secret(control_key)
        self.control_queue = control_queue
        self.stop_event = stop_event
        self.listen_url = build_listen_url(bind, port)
        self.base_url = self.listen_url.rstrip("/")
        self.instance_id = secrets.token_urlsafe(18)
        self._lock = threading.Lock()
        self._runtime: dict[str, object] = {
            "mode": MANUAL_MODE,
            "status": "idle",
            "turn_in_flight": False,
            "queued_chat_messages": 0,
            "remaining_total": 0,
            "current_task_remaining": None,
            "current_task_message": None,
            "auto_tasks": [],
        }
        self._recent_events: deque[dict[str, object]] = deque(maxlen=MAX_RECENT_EVENTS)
        self._latest_assistant: Optional[dict[str, object]] = None
        self._latest_control: Optional[dict[str, object]] = None
        self._next_event_id = 1
        self._next_request_id = 1
        self._poller_thread: Optional[threading.Thread] = None

    def start(self, initial_state: SessionState) -> None:
        with self._lock:
            self._runtime = build_state_payload(initial_state)
        self._ensure_server_running()
        self._register_instance()
        self._poller_thread = threading.Thread(
            target=self._poll_commands_loop,
            name="codex-auto-continue-control-poll",
            daemon=True,
        )
        self._poller_thread.start()

    def close(self) -> None:
        try:
            self._post_json(
                "/internal/unregister",
                {"instance_id": self.instance_id},
                timeout=1.0,
            )
        except Exception as error:
            debug(f"failed to unregister remote instance: {error}")
        if self._poller_thread is not None:
            self._poller_thread.join(timeout=1.0)

    def next_request_summary(self) -> str:
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        return f"web:{request_id}"

    def publish_runtime(self, state: SessionState) -> None:
        self._publish(runtime=build_state_payload(state))

    def publish_control_response(
        self, state: SessionState, body: str, error: bool = False
    ) -> None:
        payload = build_control_response_payload(state, body, error)
        self._publish(
            runtime=build_state_payload(state),
            recent_event=self._build_recent_event(
                "control-error" if error else "control-response",
                payload,
            ),
            latest_control=payload,
        )

    def publish_turn_complete(
        self, event: dict[str, object], state: SessionState
    ) -> None:
        payload = build_turn_notification_payload(event, state)
        self._publish(
            runtime=build_state_payload(state),
            recent_event=self._build_recent_event("assistant", payload),
            latest_assistant=payload,
        )

    def publish_system_event(self, state: SessionState, title: str, text: str) -> None:
        payload = {
            "sender": CODEX_SENDER,
            "type": "system-event",
            "title": title,
            "text": text,
            **build_state_payload(state),
        }
        self._publish(
            runtime=build_state_payload(state),
            recent_event=self._build_recent_event("system", payload),
        )

    def _publish(
        self,
        runtime: Optional[dict[str, object]] = None,
        recent_event: Optional[dict[str, object]] = None,
        latest_assistant: Optional[dict[str, object]] = None,
        latest_control: Optional[dict[str, object]] = None,
    ) -> None:
        with self._lock:
            if runtime is not None:
                self._runtime = copy.deepcopy(runtime)
            if recent_event is not None:
                self._recent_events.appendleft(copy.deepcopy(recent_event))
            if latest_assistant is not None:
                self._latest_assistant = copy.deepcopy(latest_assistant)
            if latest_control is not None:
                self._latest_control = copy.deepcopy(latest_control)
            snapshot = self._build_snapshot_locked()
        self._sync_snapshot(snapshot)

    def _build_recent_event(
        self, kind: str, payload: dict[str, object]
    ) -> dict[str, object]:
        with self._lock:
            event_id = self._next_event_id
            self._next_event_id += 1
        return {
            "id": event_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "kind": kind,
            "type": payload.get("type"),
            "title": payload.get("title") or default_event_title(kind),
            "text": coerce_notification_text(payload.get("text")) or "",
            "assistant": payload.get("assistant"),
            "user": payload.get("user"),
            "mode": payload.get("mode"),
            "status": payload.get("status"),
        }

    def _build_snapshot_locked(self) -> dict[str, object]:
        return {
            **copy.deepcopy(self._runtime),
            "control_key_hint": self.control_key_hint,
            "recent_events": list(copy.deepcopy(self._recent_events)),
            "latest_assistant": copy.deepcopy(self._latest_assistant),
            "latest_control": copy.deepcopy(self._latest_control),
        }

    def _sync_snapshot(self, snapshot: dict[str, object]) -> None:
        payload = {
            "instance_id": self.instance_id,
            "snapshot": snapshot,
        }
        try:
            self._post_json("/internal/update", payload)
        except Exception as error:
            debug(f"snapshot update failed, retrying after server recovery: {error}")
            self._recover_server()
            self._post_json("/internal/update", payload)

    def _register_instance(self) -> None:
        with self._lock:
            snapshot = self._build_snapshot_locked()
        self._post_json(
            "/internal/register",
            {
                "instance_id": self.instance_id,
                "control_key": self.control_key,
                "control_key_hint": self.control_key_hint,
                "snapshot": snapshot,
            },
        )

    def _recover_server(self) -> None:
        self._ensure_server_running()
        self._register_instance()

    def _poll_commands_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                response = self._post_json(
                    "/internal/poll",
                    {
                        "instance_id": self.instance_id,
                        "timeout_seconds": SERVER_COMMAND_POLL_TIMEOUT_SECONDS,
                    },
                    timeout=SERVER_COMMAND_POLL_TIMEOUT_SECONDS + 2.0,
                )
                command_payload = response.get("command")
                if command_payload is None:
                    continue
                if not isinstance(command_payload, dict):
                    debug(f"ignored malformed command payload: {command_payload!r}")
                    continue
                try:
                    command = parse_remote_command_message(
                        json.dumps(command_payload, ensure_ascii=False)
                    )
                except ValueError as error:
                    debug(f"ignored invalid remote command: {error}")
                    continue
                if command is None:
                    continue
                self.control_queue.put(
                    QueuedControlCommand(
                        summary=self.next_request_summary(),
                        command=command,
                    )
                )
            except Exception as error:
                if self.stop_event.is_set():
                    return
                debug(f"control poll failed, retrying after recovery: {error}")
                time.sleep(0.5)
                try:
                    self._recover_server()
                except Exception as recovery_error:
                    debug(f"server recovery failed: {recovery_error}")
                    time.sleep(1.0)

    def _ensure_server_running(self) -> None:
        if self._healthcheck():
            return

        server_path = Path(__file__).with_name(SERVER_SCRIPT)
        python_argv = [*current_python_argv(), str(server_path)]
        debug(
            "starting remote console server at "
            f"{self.listen_url} with script={server_path}"
        )
        server_process = subprocess.Popen(
            [
                *python_argv,
                "--bind",
                self.bind,
                "--port",
                str(self.port),
                "--password",
                self.password,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )

        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._healthcheck():
                return
            if server_process.poll() is not None and self._healthcheck():
                return
            time.sleep(0.1)

        raise RuntimeError(f"web console server did not become ready at {self.listen_url}")

    def _healthcheck(self) -> bool:
        try:
            payload = self._request_json("GET", "/healthz", None, timeout=1.0)
        except Exception:
            return False
        return payload.get("ok") is True

    def _post_json(
        self, path: str, payload: dict[str, object], timeout: float = SERVER_REQUEST_TIMEOUT_SECONDS
    ) -> dict[str, object]:
        return self._request_json("POST", path, payload, timeout=timeout)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, object]],
        timeout: float,
    ) -> dict[str, object]:
        request_payload = None if payload is None else encode_json(payload)
        request = urllib_request.Request(
            f"{self.base_url}{path}",
            data=request_payload,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
            except Exception:
                error_body = {}
            raise RuntimeError(
                error_body.get("error")
                or f"{method} {path} failed with status {error.code}"
            ) from error


class WebConsoleStateHub:
    def __init__(self, bind: str, port: int, password: str, control_key: str):
        self.bind = bind
        self.port = port
        self.password = password
        self.control_key = control_key
        self.control_key_hint = mask_secret(control_key)
        self.listen_url = build_listen_url(bind, port)
        self._lock = threading.Lock()
        self._runtime: dict[str, object] = {
            "mode": MANUAL_MODE,
            "status": "idle",
            "turn_in_flight": False,
            "queued_chat_messages": 0,
            "remaining_total": 0,
            "current_task_remaining": None,
            "current_task_message": None,
            "auto_tasks": [],
        }
        self._recent_events: deque[dict[str, object]] = deque(maxlen=MAX_RECENT_EVENTS)
        self._latest_assistant: Optional[dict[str, object]] = None
        self._latest_control: Optional[dict[str, object]] = None
        self._sessions: set[str] = set()
        self._subscribers: set[queue.Queue] = set()
        self._next_event_id = 1
        self._next_request_id = 1

    def next_request_summary(self) -> str:
        with self._lock:
            request_id = self._next_request_id
            self._next_request_id += 1
        return f"web:{request_id}"

    def verify_password(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate, self.password)

    def verify_control_key(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate, self.control_key)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions.add(token)
        return token

    def delete_session(self, token: Optional[str]) -> None:
        if token is None:
            return
        with self._lock:
            self._sessions.discard(token)

    def has_session(self, token: Optional[str]) -> bool:
        if token is None:
            return False
        with self._lock:
            return token in self._sessions

    def subscribe(self) -> queue.Queue:
        subscriber: queue.Queue = queue.Queue(maxsize=MAX_SSE_QUEUE_SIZE)
        with self._lock:
            self._subscribers.add(subscriber)
            snapshot = self._build_snapshot_locked()
        self._enqueue_snapshot(subscriber, snapshot)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._build_snapshot_locked()

    def publish_runtime(self, state: SessionState) -> None:
        self._publish(runtime=build_state_payload(state))

    def publish_control_response(
        self, state: SessionState, body: str, error: bool = False
    ) -> None:
        payload = build_control_response_payload(state, body, error)
        self._publish(
            runtime=build_state_payload(state),
            recent_event=self._build_recent_event(
                "control-error" if error else "control-response",
                payload,
            ),
            latest_control=payload,
        )

    def publish_turn_complete(
        self, event: dict[str, object], state: SessionState
    ) -> None:
        payload = build_turn_notification_payload(event, state)
        self._publish(
            runtime=build_state_payload(state),
            recent_event=self._build_recent_event("assistant", payload),
            latest_assistant=payload,
        )

    def publish_system_event(self, state: SessionState, title: str, text: str) -> None:
        payload = {
            "sender": CODEX_SENDER,
            "type": "system-event",
            "title": title,
            "text": text,
            **build_state_payload(state),
        }
        self._publish(
            runtime=build_state_payload(state),
            recent_event=self._build_recent_event("system", payload),
        )

    def _publish(
        self,
        runtime: Optional[dict[str, object]] = None,
        recent_event: Optional[dict[str, object]] = None,
        latest_assistant: Optional[dict[str, object]] = None,
        latest_control: Optional[dict[str, object]] = None,
    ) -> None:
        with self._lock:
            if runtime is not None:
                self._runtime = copy.deepcopy(runtime)
            if recent_event is not None:
                self._recent_events.appendleft(copy.deepcopy(recent_event))
            if latest_assistant is not None:
                self._latest_assistant = copy.deepcopy(latest_assistant)
            if latest_control is not None:
                self._latest_control = copy.deepcopy(latest_control)
            snapshot = self._build_snapshot_locked()
            subscribers = list(self._subscribers)
        self._broadcast_snapshot(subscribers, snapshot)

    def _build_recent_event(
        self, kind: str, payload: dict[str, object]
    ) -> dict[str, object]:
        with self._lock:
            event_id = self._next_event_id
            self._next_event_id += 1
        return {
            "id": event_id,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "kind": kind,
            "type": payload.get("type"),
            "title": payload.get("title") or default_event_title(kind),
            "text": coerce_notification_text(payload.get("text")) or "",
            "assistant": payload.get("assistant"),
            "user": payload.get("user"),
            "mode": payload.get("mode"),
            "status": payload.get("status"),
        }

    def _build_snapshot_locked(self) -> dict[str, object]:
        return {
            **copy.deepcopy(self._runtime),
            "listen_url": self.listen_url,
            "control_key_hint": self.control_key_hint,
            "recent_events": list(copy.deepcopy(self._recent_events)),
            "latest_assistant": copy.deepcopy(self._latest_assistant),
            "latest_control": copy.deepcopy(self._latest_control),
        }

    def _broadcast_snapshot(
        self, subscribers: list[queue.Queue], snapshot: dict[str, object]
    ) -> None:
        stale: list[queue.Queue] = []
        for subscriber in subscribers:
            if not self._enqueue_snapshot(subscriber, snapshot):
                stale.append(subscriber)
        if stale:
            with self._lock:
                for subscriber in stale:
                    self._subscribers.discard(subscriber)

    def _enqueue_snapshot(
        self, subscriber: queue.Queue, snapshot: dict[str, object]
    ) -> bool:
        event = {"type": "snapshot", "snapshot": copy.deepcopy(snapshot)}
        try:
            subscriber.put_nowait(event)
            return True
        except queue.Full:
            try:
                subscriber.get_nowait()
            except queue.Empty:
                pass
            try:
                subscriber.put_nowait(event)
                return True
            except queue.Full:
                return False


class RemoteControlHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        hub: WebConsoleStateHub,
        control_queue: queue.Queue,
        stop_event: threading.Event,
        static_dir: Path,
    ):
        super().__init__(server_address, handler_class)
        self.hub = hub
        self.control_queue = control_queue
        self.stop_event = stop_event
        self.static_dir = static_dir


class RemoteControlRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path in ("/", "/login"):
            self.serve_static(STATIC_HTML, "text/html; charset=utf-8")
            return
        if self.path == f"/{STATIC_CSS}":
            self.serve_static(STATIC_CSS, "text/css; charset=utf-8")
            return
        if self.path == f"/{STATIC_JS}":
            self.serve_static(STATIC_JS, "application/javascript; charset=utf-8")
            return
        if self.path == "/api/state":
            if not self.require_auth():
                return
            self.send_json(200, self.server.hub.snapshot())
            return
        if self.path == "/api/events":
            if not self.require_auth():
                return
            self.handle_sse()
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/login":
            self.handle_login()
            return
        if self.path == "/logout":
            self.handle_logout()
            return
        if self.path == "/api/command":
            if not self.require_auth():
                return
            self.handle_command()
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        debug(f"web {self.address_string()} {format % args}")

    def serve_static(self, filename: str, content_type: str) -> None:
        path = self.server.static_dir / filename
        if not path.exists():
            self.send_error(404)
            return
        try:
            payload = path.read_bytes()
        except OSError:
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def handle_login(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        password = coerce_notification_text(payload.get("password"))
        if password is None:
            self.send_json(400, {"error": '"password" must be a non-empty string'})
            return
        control_key = coerce_notification_text(payload.get("control_key"))
        if control_key is None:
            self.send_json(400, {"error": '"control_key" must be a non-empty string'})
            return
        if not self.server.hub.verify_password(password) or not self.server.hub.verify_control_key(
            control_key
        ):
            self.send_json(401, {"error": "invalid password or control key"})
            return
        token = self.server.hub.create_session()
        response = {"ok": True, "snapshot": self.server.hub.snapshot()}
        payload_bytes = encode_json(response)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload_bytes)))
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload_bytes)

    def handle_logout(self) -> None:
        self.server.hub.delete_session(self.current_session_token())
        payload_bytes = encode_json({"ok": True})
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload_bytes)))
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}=; HttpOnly; Max-Age=0; SameSite=Strict; Path=/",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload_bytes)

    def handle_command(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        raw_message = json.dumps(payload, ensure_ascii=False)
        try:
            command = parse_remote_command_message(raw_message)
        except ValueError as error:
            self.send_json(400, {"error": str(error)})
            return
        if command is None:
            self.send_json(400, {"error": 'control messages must include "sender":"user"'})
            return
        self.server.control_queue.put(
            QueuedControlCommand(
                summary=self.server.hub.next_request_summary(),
                command=command,
            )
        )
        self.send_json(202, {"accepted": True})

    def handle_sse(self) -> None:
        subscriber = self.server.hub.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while not self.server.stop_event.is_set():
                try:
                    event = subscriber.get(timeout=SSE_KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(b"event: snapshot\n")
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.hub.unsubscribe(subscriber)

    def read_json_body(self) -> Optional[object]:
        content_length = self.headers.get("Content-Length")
        try:
            size = int(content_length or "0")
        except ValueError:
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        if size <= 0:
            self.send_json(400, {"error": "request body must not be empty"})
            return None
        if size > MAX_REQUEST_BODY_BYTES:
            self.send_json(413, {"error": "request body is too large"})
            return None
        try:
            raw = self.rfile.read(size)
        except OSError:
            self.send_json(400, {"error": "failed to read request body"})
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "request body must be valid JSON"})
            return None

    def current_session_token(self) -> Optional[str]:
        raw_cookie = self.headers.get("Cookie")
        if not raw_cookie:
            return None
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw_cookie)
        except cookies.CookieError:
            return None
        morsel = jar.get(SESSION_COOKIE_NAME)
        if morsel is None:
            return None
        token = morsel.value.strip()
        return token or None

    def require_auth(self) -> bool:
        if self.server.hub.has_session(self.current_session_token()):
            return True
        self.send_json(401, {"error": "authentication required"})
        return False

    def send_json(self, status_code: int, payload: dict[str, object]) -> None:
        payload_bytes = encode_json(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload_bytes)


def build_listen_url(bind: str, port: int) -> str:
    if bind in ("0.0.0.0", "::"):
        return f"http://127.0.0.1:{port}/"
    host = bind if ":" not in bind or bind.startswith("[") else f"[{bind}]"
    return f"http://{host}:{port}/"


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-4:]}"


def default_event_title(kind: str) -> str:
    if kind == "assistant":
        return "Turn complete"
    if kind == "control-response":
        return "Control receipt"
    if kind == "control-error":
        return "Control error"
    return "System event"


def encode_json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


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
        return SessionState(mode=CHAT_MODE, auto_tasks=deque(), chat_queue=deque())
    return SessionState(
        mode=AUTO_MODE,
        auto_tasks=deque([AutoTask(message=prompt, remaining=limit)]),
        chat_queue=deque(),
    )


def build_state_payload(state: SessionState) -> dict[str, object]:
    task = current_auto_task(state)
    return {
        "mode": state.mode,
        "status": "executing" if state.turn_in_flight else "idle",
        "turn_in_flight": state.turn_in_flight,
        "queued_chat_messages": len(state.chat_queue),
        "remaining_total": total_auto_remaining(state.auto_tasks)
        if state.auto_tasks
        else 0,
        "current_task_remaining": None if task is None else task.remaining,
        "current_task_message": None if task is None else task.message,
        "auto_tasks": [
            {"message": task.message, "remaining": task.remaining}
            for task in state.auto_tasks
        ],
    }


def summarize_state_lines(state: SessionState) -> list[str]:
    lines = [f"mode: {state.mode}", f"status: {'executing' if state.turn_in_flight else 'idle'}"]
    lines.append(f"queued-chat-messages: {len(state.chat_queue)}")
    lines.append(
        f"remaining-total: {format_remaining_count(total_auto_remaining(state.auto_tasks)) if state.auto_tasks else '0'}"
    )
    task = current_auto_task(state)
    if task is not None:
        lines.append(f"current-task-remaining: {format_remaining_count(task.remaining)}")
        append_text_block(lines, "current-task-message", task.message)
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
        "title": "Turn complete",
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
        "title": "Control error" if error else "Control receipt",
        "text": body,
        **build_state_payload(state),
    }


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


def maybe_finalize_idle_mode(state: SessionState, hub: WebConsoleStateHub) -> None:
    if state.turn_in_flight:
        return

    if state.mode == AUTO_MODE and not state.auto_tasks:
        state.mode = MANUAL_MODE
        hub.publish_control_response(
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


def process_control_command(
    state: SessionState,
    hub: WebConsoleStateHub,
    request: QueuedControlCommand,
    current_send: Optional[ScheduledSend],
) -> bool:
    cancel_pending = should_cancel_pending_send(
        request.command, current_send, state.turn_in_flight
    )
    hub.publish_control_response(state, apply_remote_command(state, request.command))
    return cancel_pending


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


def disable_session_automation(state: SessionState) -> None:
    state.mode = MANUAL_MODE
    state.auto_tasks.clear()
    state.chat_queue.clear()


def is_local_submit_bytes(data: bytes) -> bool:
    return data.endswith(b"\r") or data.endswith(b"\n")


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


def copy_winsize_unix(source_fd: int, target_fd: int) -> None:
    try:
        packed = fcntl.ioctl(source_fd, termios.TIOCGWINSZ, b"\x00" * 8)
        fcntl.ioctl(target_fd, termios.TIOCSWINSZ, packed)
    except OSError:
        pass


def start_remote_console_client(
    bind: str,
    port: int,
    password: str,
    control_key: str,
    control_queue: queue.Queue,
    stop_event: threading.Event,
    initial_state: SessionState,
) -> RemoteConsoleClient:
    client = RemoteConsoleClient(
        bind,
        port,
        password,
        control_key,
        control_queue,
        stop_event,
    )
    client.start(initial_state)
    return client


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
            args.web_bind,
            args.web_port,
            args.web_password,
        )
    finally:
        try:
            os.close(master_fd)
        except OSError:
            pass
        notify_socket.close()


def forward_loop_unix(
    child_pid: int,
    master_fd: int,
    notify_socket: socket.socket,
    mode: str,
    prompt: str,
    limit: Optional[int],
    web_bind: str,
    web_port: int,
    web_password: str,
) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stderr_fd = sys.stderr.fileno()
    old_tty_settings = termios.tcgetattr(stdin_fd)
    session_state = build_initial_session_state(mode, prompt, limit)
    pending_send_bytes: list[tuple[float, bytes]] = []
    pending_submission: Optional[ScheduledSend] = None
    active_turn_source: Optional[str] = None
    stdin_hotkey_state = StdinHotkeyState()
    disable_allowed_at = time.monotonic() + DISABLE_GUARD_SECONDS
    stop_event = threading.Event()
    control_queue: queue.Queue = queue.Queue()
    control_key = secrets.token_urlsafe(18)
    try:
        hub = start_remote_console_client(
            web_bind,
            web_port,
            web_password,
            control_key,
            control_queue,
            stop_event,
            session_state,
        )
    except Exception as error:
        os.write(
            stderr_fd,
            (
                "[codex-auto-continue] failed to start the shared web console: "
                f"{error}\r\n"
            ).encode("utf-8"),
        )
        try:
            os.kill(child_pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            os.waitpid(child_pid, 0)
        except OSError:
            pass
        return 1

    os.write(
        stderr_fd,
        (
            "[codex-auto-continue] control key for this Codex: "
            f"{control_key}\r\n"
        ).encode("utf-8"),
    )
    os.write(
        stderr_fd,
        b"[codex-auto-continue] log in to the shared web console, then add a tab with this key.\r\n",
    )

    tty.setraw(stdin_fd)
    try:
        termios.tcflush(stdin_fd, termios.TCIFLUSH)
        debug("flushed pending stdin after launch-mode prompt")
    except termios.error as error:
        debug(f"stdin flush failed: {error}")
    copy_winsize_unix(stdin_fd, master_fd)

    def on_sigwinch(_signum, _frame) -> None:
        copy_winsize_unix(stdin_fd, master_fd)

    previous_sigwinch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, on_sigwinch)
    disable_notice_shown = False

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
                    active_turn_source = None
                    hub.publish_system_event(
                        session_state,
                        "Manual mode",
                        "switched to manual mode from local keyboard",
                    )
                    if not disable_notice_shown:
                        os.write(
                            stderr_fd,
                            b"\r\n[codex-auto-continue] switched to manual mode.\r\n",
                        )
                        disable_notice_shown = True
                    debug(f"disabled by buffered stdin hotkey {hotkey_bytes!r}")
                os.write(master_fd, hotkey_bytes)

            for control_event in drain_queue_nowait(control_queue):
                assert isinstance(control_event, QueuedControlCommand)
                debug(f"control event={control_event!r}")
                if process_control_command(
                    session_state,
                    hub,
                    control_event,
                    pending_submission,
                ):
                    pending_send_bytes.clear()
                    pending_submission = None
                    debug("cleared pending scheduled send after control update")

            maybe_finalize_idle_mode(session_state, hub)
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
                        active_turn_source = pending_submission.source
                        debug(
                            "committing scheduled submission "
                            f"{pending_submission.source}:{pending_submission.message!r}"
                        )
                        commit_scheduled_send(session_state, pending_submission)
                        pending_submission = None
                        hub.publish_runtime(session_state)
                else:
                    debug(f"sent text chunk {chunk!r}")

            timeout = None
            if pending_send_bytes:
                timeout = max(0.0, pending_send_bytes[0][0] - now)
            if stdin_hotkey_state.pending_escape_deadline is not None:
                hotkey_timeout = max(
                    0.0, stdin_hotkey_state.pending_escape_deadline - now
                )
                timeout = hotkey_timeout if timeout is None else min(timeout, hotkey_timeout)
            timeout = (
                CONTROL_POLL_INTERVAL_SECONDS
                if timeout is None
                else min(timeout, CONTROL_POLL_INTERVAL_SECONDS)
            )

            read_fds = [master_fd, notify_socket.fileno(), stdin_fd]
            ready, _, _ = select.select(read_fds, [], [], timeout)

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                os.write(stdout_fd, data)

            if stdin_fd in ready:
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
                if disable_candidate and time.monotonic() >= disable_allowed_at:
                    disable_session_automation(session_state)
                    pending_send_bytes.clear()
                    pending_submission = None
                    active_turn_source = None
                    helper_send_in_progress = False
                    hub.publish_system_event(
                        session_state,
                        "Manual mode",
                        "switched to manual mode from local keyboard",
                    )
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
                if is_local_submit_bytes(data) and not session_state.turn_in_flight:
                    session_state.turn_in_flight = True
                    active_turn_source = LOCAL_SOURCE
                    hub.publish_runtime(session_state)
                    debug("marked local turn as in flight")
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
                    completed_turn_source = active_turn_source
                    session_state.turn_in_flight = False
                    active_turn_source = None
                    if completed_turn_source in (AUTO_SOURCE, CHAT_SOURCE):
                        hub.publish_turn_complete(event, session_state)
                    elif completed_turn_source == LOCAL_SOURCE:
                        debug("suppressed local turn notification from web feed")
                    else:
                        hub.publish_turn_complete(event, session_state)
                    maybe_finalize_idle_mode(session_state, hub)
                    hub.publish_runtime(session_state)
    finally:
        stop_event.set()
        signal.signal(signal.SIGWINCH, previous_sigwinch)
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_tty_settings)
        hub.close()

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


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    parser.add_argument("--launcher", required=True)
    parser.add_argument("--mode", choices=[AUTO_MODE, CHAT_MODE], default=AUTO_MODE)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--limit", type=parse_positive_int)
    parser.add_argument("--web-bind", required=True)
    parser.add_argument("--web-port", type=parse_positive_int, required=True)
    parser.add_argument("--web-password", required=True)
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    passthrough = list(args.passthrough)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    return args, passthrough


def main() -> int:
    args, passthrough = parse_args()
    return launch_child_unix(args, passthrough)


if __name__ == "__main__":
    raise SystemExit(main())
