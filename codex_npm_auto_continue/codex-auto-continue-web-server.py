#!/usr/bin/env python3

from __future__ import annotations

import argparse
import copy
import json
import queue
import secrets
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


@dataclass
class SessionRecord:
    attached_instance_ids: list[str] = field(default_factory=list)


@dataclass
class InstanceRecord:
    instance_id: str
    control_key: Optional[str]
    control_key_hint: str
    snapshot: dict[str, object]
    command_queue: queue.Queue
    connected: bool = True
    last_seen: float = field(default_factory=time.time)


class ConsoleRegistry:
    def __init__(self, bind: str, port: int, password: str):
        self.bind = bind
        self.port = port
        self.password = password
        self.listen_url = build_listen_url(bind, port)
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        self._instances: dict[str, InstanceRecord] = {}
        self._control_key_to_instance_id: dict[str, str] = {}
        self._subscribers: dict[int, tuple[str, queue.Queue]] = {}
        self._next_subscriber_id = 1

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
            snapshot = self._build_session_snapshot_locked(token)
        self._enqueue_snapshot(subscriber, snapshot)
        return subscriber_id, subscriber

    def unsubscribe(self, subscriber_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def session_snapshot(self, token: str) -> dict[str, object]:
        with self._lock:
            return self._build_session_snapshot_locked(token)

    def attach_instance(self, token: str, control_key: str) -> tuple[str, dict[str, object]]:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                raise PermissionError("authentication required")
            instance_id = self._control_key_to_instance_id.get(control_key)
            if instance_id is None:
                raise LookupError("invalid or expired control key")
            if instance_id not in session.attached_instance_ids:
                session.attached_instance_ids.append(instance_id)
            snapshot = self._build_session_snapshot_locked(token)
        self._broadcast_session(token, snapshot)
        return instance_id, snapshot

    def detach_instance(self, token: str, instance_id: str) -> dict[str, object]:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                raise PermissionError("authentication required")
            session.attached_instance_ids = [
                attached_id
                for attached_id in session.attached_instance_ids
                if attached_id != instance_id
            ]
            snapshot = self._build_session_snapshot_locked(token)
        self._broadcast_session(token, snapshot)
        return snapshot

    def enqueue_command(
        self, token: str, instance_id: str, payload: dict[str, object]
    ) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                raise PermissionError("authentication required")
            if instance_id not in session.attached_instance_ids:
                raise PermissionError("instance is not attached in this session")
            record = self._instances.get(instance_id)
            if record is None:
                raise LookupError("instance not found")
            if not record.connected:
                raise ConnectionError("instance is offline")
            record.command_queue.put(copy.deepcopy(payload))

    def register_instance(
        self,
        instance_id: str,
        control_key: str,
        control_key_hint: str,
        snapshot: dict[str, object],
    ) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                record = InstanceRecord(
                    instance_id=instance_id,
                    control_key=control_key,
                    control_key_hint=control_key_hint,
                    snapshot=copy.deepcopy(snapshot),
                    command_queue=queue.Queue(),
                )
                self._instances[instance_id] = record
            else:
                if record.control_key and record.control_key != control_key:
                    self._control_key_to_instance_id.pop(record.control_key, None)
                record.control_key = control_key
                record.control_key_hint = control_key_hint
                record.snapshot = copy.deepcopy(snapshot)
                record.connected = True
                record.last_seen = time.time()
            self._control_key_to_instance_id[control_key] = instance_id
        self._broadcast_all()

    def update_instance(self, instance_id: str, snapshot: dict[str, object]) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                raise LookupError("instance not registered")
            record.snapshot = copy.deepcopy(snapshot)
            record.last_seen = time.time()
        self._broadcast_all()

    def unregister_instance(self, instance_id: str) -> None:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                return
            if record.control_key is not None:
                self._control_key_to_instance_id.pop(record.control_key, None)
            record.control_key = None
            record.connected = False
            record.last_seen = time.time()
        self._broadcast_all()

    def poll_command(
        self, instance_id: str, timeout_seconds: float
    ) -> Optional[dict[str, object]]:
        with self._lock:
            record = self._instances.get(instance_id)
            if record is None:
                raise LookupError("instance not registered")
            record.last_seen = time.time()
            command_queue = record.command_queue
        try:
            command = command_queue.get(timeout=max(0.0, timeout_seconds))
        except queue.Empty:
            command = None
        with self._lock:
            record = self._instances.get(instance_id)
            if record is not None:
                record.last_seen = time.time()
        return command

    def _build_session_snapshot_locked(self, token: str) -> dict[str, object]:
        session = self._sessions.get(token)
        attached_instances: list[dict[str, object]] = []
        if session is not None:
            for instance_id in session.attached_instance_ids:
                record = self._instances.get(instance_id)
                if record is None:
                    continue
                attached_instances.append(self._build_instance_payload_locked(record))
        return {
            "listen_url": self.listen_url,
            "attached_instances": attached_instances,
        }

    def _build_instance_payload_locked(self, record: InstanceRecord) -> dict[str, object]:
        payload = copy.deepcopy(record.snapshot)
        payload["instance_id"] = record.instance_id
        payload["connected"] = record.connected
        payload["control_key_hint"] = record.control_key_hint
        payload["display_name"] = f"Codex {record.control_key_hint}"
        payload["last_seen"] = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(record.last_seen)
        )
        return payload

    def _broadcast_all(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers.items())
            session_snapshots = {
                token: self._build_session_snapshot_locked(token)
                for token in {token for _id, (token, _queue) in subscribers}
                if token in self._sessions
            }
        self._broadcast_subscribers(subscribers, session_snapshots)

    def _broadcast_session(self, token: str, snapshot: dict[str, object]) -> None:
        with self._lock:
            subscribers = [
                (subscriber_id, (session_token, subscriber))
                for subscriber_id, (session_token, subscriber) in self._subscribers.items()
                if session_token == token
            ]
        self._broadcast_subscribers(subscribers, {token: snapshot})

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
            self.send_json(200, self.server.registry.session_snapshot(token))
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
        if self.path == "/api/attach":
            token = self.require_auth()
            if token is None:
                return
            self.handle_attach(token)
            return
        if self.path == "/api/detach":
            token = self.require_auth()
            if token is None:
                return
            self.handle_detach(token)
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

    def handle_attach(self, token: str) -> None:
        payload = self.read_json_body()
        if payload is None:
            return
        if not isinstance(payload, dict):
            self.send_json(400, {"error": "request body must be a JSON object"})
            return
        control_key = coerce_text(payload.get("control_key"))
        if control_key is None:
            self.send_json(400, {"error": '"control_key" must be a non-empty string'})
            return
        try:
            instance_id, snapshot = self.server.registry.attach_instance(token, control_key)
        except LookupError as error:
            self.send_json(404, {"error": str(error)})
            return
        except PermissionError as error:
            self.send_json(401, {"error": str(error)})
            return
        self.send_json(200, {"ok": True, "instance_id": instance_id, "snapshot": snapshot})

    def handle_detach(self, token: str) -> None:
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
            snapshot = self.server.registry.detach_instance(token, instance_id)
        except PermissionError as error:
            self.send_json(401, {"error": str(error)})
            return
        self.send_json(200, {"ok": True, "snapshot": snapshot})

    def handle_command(self, token: str) -> None:
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
            self.server.registry.enqueue_command(token, instance_id, command_payload)
        except PermissionError as error:
            self.send_json(403, {"error": str(error)})
            return
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
        control_key = coerce_text(payload.get("control_key"))
        control_key_hint = coerce_text(payload.get("control_key_hint"))
        snapshot = payload.get("snapshot")
        if instance_id is None:
            self.send_json(400, {"error": '"instance_id" must be a non-empty string'})
            return
        if control_key is None:
            self.send_json(400, {"error": '"control_key" must be a non-empty string'})
            return
        if control_key_hint is None:
            self.send_json(400, {"error": '"control_key_hint" must be a non-empty string'})
            return
        if not isinstance(snapshot, dict):
            self.send_json(400, {"error": '"snapshot" must be a JSON object'})
            return
        self.server.registry.register_instance(
            instance_id, control_key, control_key_hint, snapshot
        )
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=parse_positive_int, required=True)
    parser.add_argument("--password", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    registry = ConsoleRegistry(args.bind, args.port, args.password)
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
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
