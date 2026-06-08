"""Local-only HTTP server for the Blib Houdini Bridge."""

from __future__ import annotations

import json
import hmac
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from . import auth, commands, history, protocol


HOST = "127.0.0.1"
DEFAULT_PORT = 0
MAX_BODY_BYTES = 1024 * 1024

_server: "BridgeServer | None" = None


class BridgeServer:
    def __init__(self, host: str = HOST, port: int = DEFAULT_PORT, token: str | None = None):
        if host != HOST:
            raise ValueError("Blib Houdini Bridge only listens on 127.0.0.1.")
        self.host = host
        self.token = token or auth.generate_token()
        self.httpd = ThreadingHTTPServer((host, int(port)), self._handler_class())
        self.thread: threading.Thread | None = None
        self.session_path = None

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    def start(self) -> dict[str, Any]:
        if self.thread and self.thread.is_alive():
            return self.info()
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="BlibHouBridge", daemon=True)
        self.thread.start()
        self.session_path = auth.save_session(self.host, self.port, self.token, pid=os.getpid())
        return self.info()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        auth.clear_session()

    def info(self) -> dict[str, Any]:
        return {
            "running": bool(self.thread and self.thread.is_alive()),
            "host": self.host,
            "port": self.port,
            "mode": "read",
            "version": protocol.BRIDGE_VERSION,
            "session_path": str(self.session_path or auth.default_session_path()),
        }

    def _handler_class(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "BlibHouBridge/%s" % protocol.BRIDGE_VERSION

            def do_GET(self):  # noqa: N802 - stdlib handler API
                parsed = urlparse(self.path)
                if parsed.path != "/health":
                    self._send(protocol.error_response("", "", "Not found", code="not_found"), status=404)
                    return
                request_id = protocol.new_request_id()
                self._send(protocol.ok_response(request_id, "health", commands.health()))

            def do_POST(self):  # noqa: N802 - stdlib handler API
                parsed = urlparse(self.path)
                if parsed.path != "/rpc":
                    self._send(protocol.error_response("", "", "Not found", code="not_found"), status=404)
                    return
                started_at = time.time()
                request_id = ""
                command = ""
                status = 200
                response_payload = None
                try:
                    raw = self._read_json()
                    request = protocol.parse_request(raw)
                    request_id = request["request_id"]
                    command = request["command"]
                    self._require_token(request.get("token"))
                    result = commands.execute_in_houdini(command, request["payload"])
                    response_payload = protocol.ok_response(request_id, command, result)
                    self._send(response_payload)
                except protocol.BridgeProtocolError as exc:
                    status = 400
                    response_payload = protocol.error_response(request_id, command, str(exc), code="bad_request")
                    self._send(response_payload, status=status)
                except PermissionError as exc:
                    status = 401
                    response_payload = protocol.error_response(request_id, command, str(exc), code="unauthorized")
                    self._send(response_payload, status=status)
                except Exception as exc:
                    status = 500
                    response_payload = protocol.error_response(request_id, command, str(exc), code="command_failed")
                    self._send(response_payload, status=status)
                finally:
                    self._record_rpc(parsed.path, request_id, command, status, response_payload, started_at)

            def log_message(self, format, *args):  # noqa: A002 - stdlib handler API
                return

            def _read_json(self):
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError as exc:
                    raise protocol.BridgeProtocolError("Invalid Content-Length header.") from exc
                if length <= 0:
                    return {}
                if length > MAX_BODY_BYTES:
                    raise protocol.BridgeProtocolError("Request body is too large.")
                data = self.rfile.read(length)
                try:
                    return json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise protocol.BridgeProtocolError("Request body must be valid JSON.") from exc

            def _require_token(self, body_token):
                header_token = self.headers.get("X-Blib-Bridge-Token")
                if not _token_matches(header_token, bridge.token) or not _token_matches(body_token, bridge.token):
                    raise PermissionError("Invalid bridge token.")

            def _send(self, payload, status=200):
                body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _record_rpc(self, path, request_id, command, status, response_payload, started_at):
                error = (response_payload or {}).get("error") or {}
                history.record(
                    {
                        "path": path,
                        "client": self.client_address[0] if self.client_address else "",
                        "request_id": request_id,
                        "command": command,
                        "ok": bool((response_payload or {}).get("ok")),
                        "status": status,
                        "error_code": error.get("code"),
                        "error_message": error.get("message"),
                        "duration_ms": round((time.time() - started_at) * 1000, 3),
                    }
                )

        return Handler


def start_server(port: int = DEFAULT_PORT) -> dict[str, Any]:
    global _server
    if _server is not None:
        return _server.info()
    _server = BridgeServer(port=port)
    return _server.start()


def stop_server() -> bool:
    global _server
    if _server is None:
        return False
    _server.stop()
    _server = None
    return True


def status() -> dict[str, Any]:
    if _server is None:
        return {
            "running": False,
            "host": HOST,
            "mode": "read",
            "version": protocol.BRIDGE_VERSION,
            "session_path": str(auth.default_session_path()),
        }
    return _server.info()


def _token_matches(value: Any, expected: str) -> bool:
    return isinstance(value, str) and hmac.compare_digest(value, expected)
