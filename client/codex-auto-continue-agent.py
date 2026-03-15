#!/usr/bin/env python3

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

LOCAL_MANAGER_BIND = "127.0.0.1"
LOCAL_MANAGER_START_TIMEOUT_SECONDS = 10.0
LOCAL_MANAGER_REQUEST_TIMEOUT_SECONDS = 5.0
LOCAL_MANAGER_STOP_TIMEOUT_SECONDS = 5.0
REMOTE_POLL_TIMEOUT_SECONDS = 15.0
REMOTE_REQUEST_TIMEOUT_SECONDS = REMOTE_POLL_TIMEOUT_SECONDS + 5.0
SNAPSHOT_PUSH_INTERVAL_SECONDS = 2.0
RETRY_DELAY_SECONDS = 2.0
MACHINE_KEY_BYTES = 24


def encode_json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def parse_positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def normalize_server_url(value: str) -> str:
    parsed = urllib_parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("server URL must be an absolute http(s) URL")
    normalized = parsed._replace(path=parsed.path.rstrip("/") or "", params="", query="", fragment="")
    return urllib_parse.urlunparse(normalized)


def current_python_argv() -> list[str]:
    if sys.executable:
        return [sys.executable]
    return ["python3"]


def choose_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOCAL_MANAGER_BIND, 0))
        return int(sock.getsockname()[1])


def read_or_create_machine_key(path: Path) -> str:
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    machine_key = secrets.token_urlsafe(MACHINE_KEY_BYTES)
    path.write_text(machine_key + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return machine_key


class JsonHttpClient:
    def __init__(self, base_url: str, with_cookies: bool = False):
        self.base_url = base_url.rstrip("/")
        if with_cookies:
            self._cookie_jar = http.cookiejar.CookieJar()
            self._opener = urllib_request.build_opener(
                urllib_request.HTTPCookieProcessor(self._cookie_jar)
            )
        else:
            self._cookie_jar = None
            self._opener = urllib_request.build_opener()

    def request_json(
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
            with self._opener.open(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib_error.HTTPError as error:
            message = f"{method} {path} failed with status {error.code}"
            try:
                body = json.loads(error.read().decode("utf-8"))
            except Exception:
                body = {}
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                message = body["error"]
            raise RuntimeError(message) from error


class LocalManagerBridge:
    def __init__(
        self,
        manager_script: Path,
        launch_script: str,
        child_passthrough: list[str],
    ):
        self.manager_script = manager_script
        self.launch_script = launch_script
        self.child_passthrough = list(child_passthrough)
        self.port = choose_local_port()
        self.password = secrets.token_urlsafe(24)
        self.base_url = f"http://{LOCAL_MANAGER_BIND}:{self.port}"
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._client = JsonHttpClient(self.base_url, with_cookies=True)

    def ensure_running(self) -> None:
        with self._lock:
            process = self._process
            if process is not None and process.poll() is None and self._healthcheck():
                return
            self._restart_locked()

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        self._stop_process(process)

    def snapshot(self) -> dict[str, object]:
        self.ensure_running()
        try:
            return self._client.request_json(
                "GET", "/api/state", None, timeout=LOCAL_MANAGER_REQUEST_TIMEOUT_SECONDS
            )
        except RuntimeError as error:
            if "authentication required" not in str(error):
                raise
        self._login()
        return self._client.request_json(
            "GET", "/api/state", None, timeout=LOCAL_MANAGER_REQUEST_TIMEOUT_SECONDS
        )

    def create_instance(self) -> dict[str, object]:
        return self._post_with_auth("/api/instances", {})

    def terminate_instance(self, instance_id: str) -> dict[str, object]:
        return self._post_with_auth("/api/terminate", {"instance_id": instance_id})

    def send_instance_command(
        self, instance_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        request_payload = {"instance_id": instance_id, **payload}
        return self._post_with_auth("/api/command", request_payload)

    def _post_with_auth(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.ensure_running()
        try:
            return self._client.request_json(
                "POST", path, payload, timeout=LOCAL_MANAGER_REQUEST_TIMEOUT_SECONDS
            )
        except RuntimeError as error:
            if "authentication required" not in str(error):
                raise
        self._login()
        return self._client.request_json(
            "POST", path, payload, timeout=LOCAL_MANAGER_REQUEST_TIMEOUT_SECONDS
        )

    def _restart_locked(self) -> None:
        self._stop_process(self._process)
        argv = [
            *current_python_argv(),
            str(self.manager_script),
            "--bind",
            LOCAL_MANAGER_BIND,
            "--port",
            str(self.port),
            "--password",
            self.password,
            "--launch-script",
            self.launch_script,
            "--",
            *self.child_passthrough,
        ]
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        self._process = process

        deadline = time.monotonic() + LOCAL_MANAGER_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"local manager exited with status {process.returncode}")
            if self._healthcheck():
                self._login()
                return
            time.sleep(0.1)

        raise RuntimeError(f"local manager did not become ready at {self.base_url}/")

    def _healthcheck(self) -> bool:
        try:
            payload = self._client.request_json(
                "GET", "/healthz", None, timeout=1.0
            )
        except Exception:
            return False
        return payload.get("ok") is True

    def _login(self) -> None:
        self._client.request_json(
            "POST",
            "/login",
            {"password": self.password},
            timeout=LOCAL_MANAGER_REQUEST_TIMEOUT_SECONDS,
        )

    def _stop_process(self, process: Optional[subprocess.Popen]) -> None:
        if process is None:
            return
        if process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGINT)
        except OSError:
            return
        try:
            process.wait(timeout=LOCAL_MANAGER_STOP_TIMEOUT_SECONDS)
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


class MachineAgent:
    def __init__(
        self,
        server_url: str,
        machine_key: str,
        machine_name: str,
        manager: LocalManagerBridge,
    ):
        self.server_url = server_url.rstrip("/")
        self.machine_key = machine_key
        self.machine_name = machine_name
        self.manager = manager
        self.remote = JsonHttpClient(self.server_url, with_cookies=False)
        self.stop_event = threading.Event()
        self._threads: list[threading.Thread] = []

    def run(self) -> int:
        self._print_startup()
        self.manager.ensure_running()
        self._push_snapshot()

        self._threads = [
            threading.Thread(
                target=self._snapshot_loop,
                name="codex-auto-continue-agent-sync",
                daemon=True,
            ),
            threading.Thread(
                target=self._command_loop,
                name="codex-auto-continue-agent-poll",
                daemon=True,
            ),
        ]
        for thread in self._threads:
            thread.start()

        while not self.stop_event.is_set():
            time.sleep(0.2)

        self._cleanup()
        return 0

    def request_stop(self) -> None:
        self.stop_event.set()

    def _print_startup(self) -> None:
        print(f"[codex-auto-continue] remote server: {self.server_url}/")
        print(f"[codex-auto-continue] machine name: {self.machine_name}")
        print(f"[codex-auto-continue] machine key: {self.machine_key}")

    def _snapshot_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._push_snapshot()
            except Exception as error:
                self._log_error(f"snapshot sync failed: {error}")
            self.stop_event.wait(SNAPSHOT_PUSH_INTERVAL_SECONDS)

    def _command_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                response = self.remote.request_json(
                    "POST",
                    "/internal/machines/poll",
                    {
                        "machine_key": self.machine_key,
                        "timeout_seconds": REMOTE_POLL_TIMEOUT_SECONDS,
                    },
                    timeout=REMOTE_REQUEST_TIMEOUT_SECONDS,
                )
                command = response.get("command")
                if command is None:
                    continue
                if not isinstance(command, dict):
                    self._log_error(f"ignored malformed remote command: {command!r}")
                    continue
                self._apply_command(command)
            except Exception as error:
                if "machine not registered" in str(error):
                    try:
                        self._push_snapshot()
                        continue
                    except Exception as sync_error:
                        self._log_error(f"machine re-register failed: {sync_error}")
                self._log_error(f"remote command poll failed: {error}")
                self.stop_event.wait(RETRY_DELAY_SECONDS)

    def _apply_command(self, command: dict[str, object]) -> None:
        kind = command.get("kind")
        if kind == "create_instance":
            self.manager.create_instance()
        elif kind == "terminate_instance":
            instance_id = coerce_text(command.get("instance_id"))
            if instance_id is None:
                raise RuntimeError("terminate_instance command is missing instance_id")
            self.manager.terminate_instance(instance_id)
        elif kind == "instance_command":
            instance_id = coerce_text(command.get("instance_id"))
            payload = command.get("payload")
            if instance_id is None:
                raise RuntimeError("instance_command is missing instance_id")
            if not isinstance(payload, dict):
                raise RuntimeError("instance_command is missing payload")
            self.manager.send_instance_command(instance_id, payload)
        else:
            raise RuntimeError(f"unsupported machine command: {kind!r}")

        self._push_snapshot()

    def _cleanup(self) -> None:
        try:
            self.remote.request_json(
                "POST",
                "/internal/machines/unregister",
                {"machine_key": self.machine_key},
                timeout=5.0,
            )
        except Exception:
            pass
        self.manager.stop()

    def _push_snapshot(self) -> None:
        snapshot = self.manager.snapshot()
        self.remote.request_json(
            "POST",
            "/internal/machines/update",
            {
                "machine_key": self.machine_key,
                "machine_name": self.machine_name,
                "snapshot": snapshot,
            },
            timeout=REMOTE_REQUEST_TIMEOUT_SECONDS,
        )

    def _log_error(self, message: str) -> None:
        print(f"[codex-auto-continue] {message}", file=sys.stderr)


def coerce_text(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.replace("\r\n", "\n").strip()
    return normalized or None


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", required=True, type=normalize_server_url)
    parser.add_argument("--machine-key-file", required=True)
    parser.add_argument("--machine-name")
    parser.add_argument("--launch-script", required=True)
    parser.add_argument("passthrough", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    passthrough = list(args.passthrough)
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    return args, passthrough


def main() -> int:
    args, passthrough = parse_args()
    machine_key = read_or_create_machine_key(Path(args.machine_key_file))
    machine_name = coerce_text(args.machine_name) or socket.gethostname()
    manager = LocalManagerBridge(
        Path(__file__).with_name("codex-auto-continue-web-server.py"),
        args.launch_script,
        passthrough,
    )
    agent = MachineAgent(args.server_url, machine_key, machine_name, manager)

    def request_stop(_signum, _frame) -> None:
        agent.request_stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        return agent.run()
    finally:
        agent.request_stop()


if __name__ == "__main__":
    raise SystemExit(main())
