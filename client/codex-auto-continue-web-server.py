#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import json
import queue
import secrets
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

SESSION_COOKIE_NAME = "codex_remote_session"
STATIC_HTML = "codex-auto-continue-web.html"
STATIC_CSS = "codex-auto-continue-web.css"
STATIC_JS = "codex-auto-continue-web.js"
SSE_KEEPALIVE_SECONDS = 15.0
MAX_REQUEST_BODY_BYTES = 65536
MAX_SSE_QUEUE_SIZE = 32
LOCAL_CLIENTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
DEFAULT_INSTANCE_PROMPT = "继续"


@dataclass
class SessionRecord:
    created_at: float = field(default_factory=time.time)


@dataclass
class InstanceRecord:
    instance_id: str
    display_name: str
    snapshot: dict[str, object]
    command_queue: queue.Queue
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    connected: bool = False
    lifecycle_state: str = "starting"
    hidden: bool = False
    spawned_by_server: bool = False
    launch_error: Optional[str] = None
    exit_code: Optional[int] = None
    pid: Optional[int] = None
    process: Optional[subprocess.Popen] = None
    log_path: Optional[str] = None


class ConsoleRegistry:
    def __init__(
        self,
        bind: str,
        port: int,
        password: str,
        launch_script: Optional[str],
        child_passthrough: list[str],
    ):
        self.bind = bind
        self.port = port
        self.password = password
        self.launch_script = launch_script
        self.child_passthrough = list(child_passthrough)
        self.listen_url = build_listen_url(bind, port)
        self.static_dir = Path(__file__).resolve().parent
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._instances: dict[str, InstanceRecord] = {}
        self._subscribers: dict[int, tuple[str, queue.Queue]] = {}
        self._next_subscriber_id = 1
        self._next_instance_number = 1

    def verify_password(self, candidate: str) -> bool:
        return secrets.compare_digest(candidate, self.password)

    def create_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = SessionRecord()
        return token

    def delete_session(self, token: Optional[str]) -> None:
        if token is None:
            return
        with self._lock:
            self._sessions.pop(token, None)
            stale_ids = [
                subscriber_id
                for subscriber_id, (session_token, _subscriber) in self._subscribers.items()
                if session_token == token
            ]
            for subscriber_id in stale_ids:
                self._subscribers.pop(subscriber_id, None)

    def has_session(self, token: Optional[str]) -> bool:
        if token is None:
            return False
        with self._lock:
            return token in self._sessions

    def subscribe(self, token: str) -> tuple[int, queue.Queue]:
        subscriber: queue.Queue = queue.Queue(maxsize=MAX_SSE_QUEUE_SIZE)
        with self._lock:
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            self._subscribers[subscriber_id] = (token, subscriber)
            snapshot = self._build_session_snapshot_locked()
        self._enqueue_snapshot(subscriber, snapshot)
        return subscriber_id, subscriber

    def unsubscribe(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def session_snapshot(self, token: str) -> dict[str, object]:
        with self._lock:
            if token not in self._sessions:
                raise PermissionError("authentication required")
            return self._build_session_snapshot_locked()

    def create_instance(self) -> tuple[str, dict[str, object]]:
        if self.launch_script is None:
            raise RuntimeError(
                "instance creation is unavailable because the manager was started without a launch script"
            )

        with self._lock:
            instance_id = secrets.token_urlsafe(18)
            display_name = self._next_display_name_locked()
            record = InstanceRecord(
                instance_id=instance_id,
                display_name=display_name,
                snapshot=default_instance_snapshot(),
                command_queue=queue.Queue(),
                connected=False,
                lifecycle_state="starting",
                spawned_by_server=True,
            )
            self._instances[instance_id] = record
            snapshot = self._build_session_snapshot_locked()
        self._broadcast_all()

        try:
            self._spawn_instance(record)
        except Exception as error:
            with self._lock:
                current = self._instances.get(instance_id)
                if current is not None:
                    current.connected = False
                    current.lifecycle_state = "failed"
                    current.launch_error = str(error)
                    current.snapshot = default_instance_snapshot()
                    snapshot = self._build_session_snapshot_locked()
                else:
                    snapshot = self._build_session_snapshot_locked()
            self._broadcast_all()
            raise

        return instance_id, snapshot

    def terminate_instance(self, instance_id: str) -> dict[str, object]:
        process: Optional[subprocess.Popen] = None
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None or record.hidden:
                raise LookupError("instance not found")
            process = record.process
            if process is None:
                record.hidden = True
                if not record.connected:
                    self._instances.pop(instance_id, None)
            else:
                record.hidden = True
                record.lifecycle_state = "stopping"
                record.connected = False
            snapshot = self._build_session_snapshot_locked()
        self._broadcast_all()

        if process is not None and process.poll() is None:
            self._terminate_helper_process(process)
        return snapshot

    def shutdown(self) -> None:
        with self._lock:
            processes = [
                record.process
                for record in self._instances.values()
                if record.spawned_by_server and record.process is not None
            ]
        for process in processes:
            if process is None:
                continue
            self._terminate_helper_process(process)

    def enqueue_command(self, instance_id: str, payload: dict[str, object]) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None or record.hidden:
                raise LookupError("instance not found")
            if not record.connected:
                raise ConnectionError("instance is offline")
            record.command_queue.put(copy.deepcopy(payload))

    def register_instance(self, instance_id: str, snapshot: dict[str, object]) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                record = InstanceRecord(
                    instance_id=instance_id,
                    display_name=self._next_display_name_locked(),
                    snapshot=copy.deepcopy(snapshot),
                    command_queue=queue.Queue(),
                )
                self._instances[instance_id] = record
            record.snapshot = copy.deepcopy(snapshot)
            record.connected = True
            record.hidden = False
            record.last_seen = time.time()
            record.lifecycle_state = lifecycle_from_snapshot(snapshot)
            record.launch_error = None
        self._broadcast_all()

    def update_instance(self, instance_id: str, snapshot: dict[str, object]) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                raise LookupError("instance not registered")
            record.snapshot = copy.deepcopy(snapshot)
            record.connected = True
            record.last_seen = time.time()
            record.lifecycle_state = lifecycle_from_snapshot(snapshot)
            record.launch_error = None
        self._broadcast_all()

    def unregister_instance(self, instance_id: str) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                return
            record.connected = False
            record.last_seen = time.time()
            record.lifecycle_state = "exited"
            if record.hidden and record.process is None:
                self._instances.pop(instance_id, None)
        self._broadcast_all()

    def poll_command(
        self, instance_id: str, timeout_seconds: float
    ) -> Optional[dict[str, object]]:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                raise LookupError("instance not registered")
            command_queue = record.command_queue
            record.last_seen = time.time()
        try:
            command = command_queue.get(timeout=max(0.0, timeout_seconds))
        except queue.Empty:
            command = None
        with self._lock:
            record = self._instances.get(instance_id)
            if record is not None:
                record.last_seen = time.time()
        return command

    def _spawn_instance(self, record: InstanceRecord) -> None:
        if self.launch_script is None:
            raise RuntimeError("missing launch script")

        log_path = Path(tempfile.gettempdir()) / (
            f"codex-auto-continue-{record.instance_id}.log"
        )
        log_handle = open(log_path, "ab")
        argv = [
            self.launch_script,
            "codex",
            "--mode",
            "chat",
            "--prompt",
            DEFAULT_INSTANCE_PROMPT,
            "--web-bind",
            self.bind,
            "--web-port",
            str(self.port),
            "--web-password",
            self.password,
            "--instance-id",
            record.instance_id,
            "--",
            *self.child_passthrough,
        ]
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        except Exception:
            log_handle.close()
            raise

        with self._lock:
            current = self._instances.get(record.instance_id)
            if current is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                log_handle.close()
                return
            current.process = process
            current.pid = process.pid
            current.log_path = str(log_path)

        watcher = threading.Thread(
            target=self._wait_for_instance_exit,
            args=(record.instance_id, process, log_handle),
            name=f"codex-auto-continue-wait-{record.instance_id}",
            daemon=True,
        )
        watcher.start()

    def _wait_for_instance_exit(
        self, instance_id: str, process: subprocess.Popen, log_handle
    ) -> None:
        try:
            exit_code = process.wait()
        finally:
            log_handle.close()

        should_broadcast = False
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                return
            record.process = None
            record.pid = None
            record.connected = False
            record.exit_code = exit_code
            record.last_seen = time.time()
            if record.hidden:
                self._instances.pop(instance_id, None)
            else:
                record.lifecycle_state = "exited" if exit_code == 0 else "failed"
                if exit_code != 0 and record.launch_error is None:
                    detail = f"Codex exited with status {exit_code}"
                    if record.log_path:
                        log_tail = tail_text_file(Path(record.log_path))
                        if log_tail:
                            detail = f"{detail}\n\n{log_tail}"
                    record.launch_error = detail
            should_broadcast = True

        if should_broadcast:
            self._broadcast_all()

    def _terminate_helper_process(
        self, process: subprocess.Popen, timeout_seconds: float = 5.0
    ) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=timeout_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            process.kill()
        except OSError:
            return
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            return

    def _next_display_name_locked(self) -> str:
        value = self._next_instance_number
        self._next_instance_number += 1
        return f"Codex {value}"

    def _build_session_snapshot_locked(self) -> dict[str, object]:
        instances = [
            self._build_instance_payload_locked(record)
            for record in self._sorted_visible_instances_locked()
        ]
        return {
            "listen_url": self.listen_url,
            "can_create_instances": self.launch_script is not None,
            "instances": instances,
        }

    def _sorted_visible_instances_locked(self) -> list[InstanceRecord]:
        return sorted(
            (
                record
                for record in self._instances.values()
                if not record.hidden
            ),
            key=lambda record: record.created_at,
        )

    def _build_instance_payload_locked(self, record: InstanceRecord) -> dict[str, object]:
        payload = copy.deepcopy(record.snapshot)
        payload["instance_id"] = record.instance_id
        payload["connected"] = record.connected
        payload["display_name"] = record.display_name
        payload["lifecycle_state"] = record.lifecycle_state
        payload["last_seen"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(record.last_seen)
        )
        payload["spawned_by_server"] = record.spawned_by_server
        payload["launch_error"] = record.launch_error
        payload["pid"] = record.pid
        return payload

    def _broadcast_all(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers.items())
            session_snapshots = {
                token: self._build_session_snapshot_locked()
                for token in {token for _subscriber_id, (token, _queue) in subscribers}
                if token in self._sessions
            }
        self._broadcast_subscribers(subscribers, session_snapshots)

    def _broadcast_subscribers(
        self,
        subscribers: list[tuple[int, tuple[str, queue.Queue]]],
        session_snapshots: dict[str, dict[str, object]],
    ) -> None:
        stale_ids: list[int] = []
        for subscriber_id, (token, subscriber) in subscribers:
            snapshot = session_snapshots.get(token)
            if snapshot is None:
                stale_ids.append(subscriber_id)
                continue
            if not self._enqueue_snapshot(subscriber, snapshot):
                stale_ids.append(subscriber_id)
        if stale_ids:
            with self._lock:
                for subscriber_id in stale_ids:
                    self._subscribers.pop(subscriber_id, None)

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


class RemoteConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        registry: ConsoleRegistry,
        static_dir: Path,
    ):
        super().__init__(server_address, handler_class)
        self.registry = registry
        self.static_dir = static_dir


class RemoteConsoleRequestHandler(BaseHTTPRequestHandler):
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
        if self.path == "/healthz":
            self.send_json(200, {"ok": True})
            return
        if self.path == "/api/state":
            token = self.require_auth()
            if token is None:
                return
            try:
                snapshot = self.server.registry.session_snapshot(token)
            except PermissionError as error:
                self.send_json(401, {"error": str(error)})
                return
            self.send_json(200, snapshot)
            return
        if self.path == "/api/events":
            token = self.require_auth()
            if token is None:
                return
            self.handle_sse(token)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/login":
            self.handle_login()
            return
        if self.path == "/logout":
            self.handle_logout()
            return
        if self.path == "/api/instances":
            token = self.require_auth()
            if token is None:
                return
            self.handle_create_instance(token)
            return
        if self.path == "/api/terminate":
            token = self.require_auth()
            if token is None:
                return
            self.handle_terminate_instance(token)
            return
        if self.path == "/api/command":
            token = self.require_auth()
            if token is None:
                return
            self.handle_command(token)
            return
        if self.path == "/internal/register":
            if not self.require_local_internal():
                return
            self.handle_internal_register()
            return
        if self.path == "/internal/update":
            if not self.require_local_internal():
                return
            self.handle_internal_update()
            return
        if self.path == "/internal/unregister":
            if not self.require_local_internal():
                return
            self.handle_internal_unregister()
            return
        if self.path == "/internal/poll":
            if not self.require_local_internal():
                return
            self.handle_internal_poll()
            return
        self.send_error(404)

    def log_message(self, _format: str, *_args: object) -> None:
        return

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
        password = coerce_text(payload.get("password"))
        if password is None:
            self.send_json(400, {"error": '"password" must be a non-empty string'})
            return
        if not self.server.registry.verify_password(password):
            self.send_json(401, {"error": "invalid password"})
            return
        token = self.server.registry.create_session()
        response = {"ok": True, "snapshot": self.server.registry.session_snapshot(token)}
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
        self.server.registry.delete_session(self.current_session_token())
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

    def handle_create_instance(self, _token: str) -> None:
        payload = self.read_json_body(allow_empty=True)
        if payload is None:
            return
        if payload not in ({}, None) and not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        try:
            instance_id, snapshot = self.server.registry.create_instance()
        except RuntimeError as error:
            self.send_json(503, {"error": str(error)})
            return
        self.send_json(201, {"ok": True, "instance_id": instance_id, "snapshot": snapshot})

    def handle_terminate_instance(self, _token: str) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        instance_id = coerce_text(payload.get("instance_id"))
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        try:
            snapshot = self.server.registry.terminate_instance(instance_id)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
            return
        self.send_json(200, {"ok": True, "snapshot": snapshot})

    def handle_command(self, _token: str) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        instance_id = coerce_text(payload.get("instance_id"))
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        command_payload = copy.deepcopy(payload)
        command_payload.pop("instance_id", None)
        if command_payload.get("sender") != "user":
            self.send_json(400, {"error": 'control messages must include "sender":"user"'})
            return
        try:
            self.server.registry.enqueue_command(instance_id, command_payload)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
            return
        except ConnectionError as error:
            self.send_json(409, {"error": str(error)})
            return
        self.send_json(202, {"accepted": True})

    def handle_internal_register(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        instance_id = coerce_text(payload.get("instance_id"))
        snapshot = payload.get("snapshot")
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        if not isinstance(snapshot, dict):
            self.send_json(400, {"error": '"snapshot" must be a JSON object'})
            return
        self.server.registry.register_instance(instance_id, snapshot)
        self.send_json(200, {"ok": True})

    def handle_internal_update(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        instance_id = coerce_text(payload.get("instance_id"))
        snapshot = payload.get("snapshot")
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        if not isinstance(snapshot, dict):
            self.send_json(400, {"error": '"snapshot" must be a JSON object'})
            return
        try:
            self.server.registry.update_instance(instance_id, snapshot)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
            return
        self.send_json(200, {"ok": True})

    def handle_internal_unregister(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        instance_id = coerce_text(payload.get("instance_id"))
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        self.server.registry.unregister_instance(instance_id)
        self.send_json(200, {"ok": True})

    def handle_internal_poll(self) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        instance_id = coerce_text(payload.get("instance_id"))
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        timeout_seconds = payload.get("timeout_seconds", 15)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 0
            or timeout_seconds > 60
        ):
            self.send_json(
                400, {"error": '"timeout_seconds" must be a number between 0 and 60'}
            )
            return
        try:
            command = self.server.registry.poll_command(instance_id, float(timeout_seconds))
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
            return
        self.send_json(200, {"ok": True, "command": command})

    def handle_sse(self, token: str) -> None:
        subscriber_id, subscriber = self.server.registry.subscribe(token)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
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
            self.server.registry.unsubscribe(subscriber_id)

    def require_local_internal(self) -> bool:
        if self.client_address[0] in LOCAL_CLIENTS:
            return True
        self.send_json(403, {"error": "internal endpoint is localhost-only"})
        return False

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

    def require_auth(self) -> Optional[str]:
        token = self.current_session_token()
        if self.server.registry.has_session(token):
            return token
        self.send_json(401, {"error": "authentication required"})
        return None

    def read_json_body(self, allow_empty: bool = False) -> Optional[object]:
        content_length = self.headers.get("Content-Length")
        try:
            size = int(content_length or "0")
        except ValueError:
            self.send_json(400, {"error": "invalid Content-Length"})
            return None
        if size <= 0:
            if allow_empty:
                return {}
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

    def send_json(self, status_code: int, payload: dict[str, object]) -> None:
        payload_bytes = encode_json(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload_bytes)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def build_listen_url(bind: str, port: int) -> str:
    if bind in ("0.0.0.0", "::"):
        return f"http://127.0.0.1:{port}/"
    host = bind if ":" not in bind or bind.startswith("[") else f"[{bind}]"
    return f"http://{host}:{port}/"


def coerce_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\r\n", "\n").strip()
    return normalized or None


def encode_json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def default_instance_snapshot() -> dict[str, object]:
    return {
        "mode": "chat",
        "status": "idle",
        "turn_in_flight": False,
        "queued_chat_messages": 0,
        "remaining_total": 0,
        "current_task_remaining": None,
        "current_task_message": None,
        "auto_tasks": [],
        "recent_events": [],
        "latest_assistant": None,
        "latest_control": None,
    }


def lifecycle_from_snapshot(snapshot: dict[str, object]) -> str:
    if snapshot.get("status") == "executing" or snapshot.get("turn_in_flight") is True:
        return "running"
    return "idle"


def tail_text_file(path: Path, max_bytes: int = 4096) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            raw = handle.read()
    except OSError:
        return None
    try:
        text = raw.decode("utf-8", errors="replace").strip()
    except Exception:
        return None
    return text or None


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=parse_positive_int, required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--launch-script")
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    passthrough = list(args.passthrough)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]

    return args, passthrough


def main() -> int:
    args, passthrough = parse_args()
    registry = ConsoleRegistry(
        args.bind,
        args.port,
        args.password,
        args.launch_script,
        passthrough,
    )
    server = RemoteConsoleHTTPServer(
        (args.bind, args.port),
        RemoteConsoleRequestHandler,
        registry,
        Path(__file__).resolve().parent,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        registry.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
