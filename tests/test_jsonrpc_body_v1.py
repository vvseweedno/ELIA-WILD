from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from elia.body.protocols import JSONRPCBody


ADD_PARAMS_SCHEMA = {
    "type": "object",
    "properties": {
        "a": {"type": "integer"},
        "b": {"type": "integer"},
    },
    "required": ["a", "b"],
    "additionalProperties": False,
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003 - stdlib signature
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        size = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(size))
        if request["method"] == "add":
            params = request.get("params") or {}
            result = int(params["a"]) + int(params["b"])
            payload = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        else:
            payload = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": "method not found"},
            }
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_jsonrpc_real_http_roundtrip_and_method_allowlist() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        body = JSONRPCBody(
            {
                "enabled": True,
                "endpoints": {
                    "local": {
                        "enabled": True,
                        "url": f"http://{host}:{port}",
                        "allow_private": True,
                        "allowed_methods": ["add"],
                        "method_param_schemas": {"add": ADD_PARAMS_SCHEMA},
                        "timeout_seconds": 2,
                    }
                },
            }
        )
        result = body.call("local", "add", {"a": 20, "b": 22})
        assert result.ok is True
        assert result.data["result"] == 42
        assert result.data["peer_ip"] == "127.0.0.1"

        denied = body.call("local", "delete_everything", {})
        assert denied.ok is False
        assert "not allow-listed" in (denied.error or "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_jsonrpc_rejects_oversized_model_controlled_request_before_network() -> None:
    body = JSONRPCBody(
        {
            "enabled": True,
            "endpoints": {
                "bounded": {
                    "enabled": True,
                    "url": "http://127.0.0.1:9",
                    "allow_private": True,
                    "allowed_methods": ["add"],
                    "method_param_schemas": {"add": ADD_PARAMS_SCHEMA},
                    "max_request_bytes": 1024,
                }
            },
        }
    )
    result = body.call("bounded", "add", {"a": "x" * 5000, "b": 1})
    assert result.ok is False
    assert "does not match configured type" in (result.error or "")


def test_jsonrpc_rejects_out_of_scope_params_before_network() -> None:
    body = JSONRPCBody(
        {
            "enabled": True,
            "endpoints": {
                "bounded": {
                    "enabled": True,
                    "url": "http://127.0.0.1:9",
                    "allow_private": True,
                    "allowed_methods": ["add"],
                    "method_param_schemas": {"add": ADD_PARAMS_SCHEMA},
                }
            },
        }
    )
    result = body.call("bounded", "add", {"a": 1, "b": 2, "command": "extra"})
    assert result.ok is False
    assert "out-of-scope fields" in (result.error or "")
