from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

from elia.body.protocols import JSONRPCBody


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
                        "timeout_seconds": 2,
                    }
                },
            }
        )
        result = body.call("local", "add", {"a": 20, "b": 22})
        assert result.ok is True
        assert result.data["result"] == 42

        denied = body.call("local", "delete_everything", {})
        assert denied.ok is False
        assert "not allow-listed" in (denied.error or "")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
