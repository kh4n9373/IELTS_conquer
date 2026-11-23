import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from e2e.ports import PROTECTED_PORTS, PortBusyError, assert_port_free, pick_free_port, wait_for_owned_server


def test_refuses_busy_port() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(PortBusyError):
            assert_port_free(s.getsockname()[1])


def test_refuses_protected_port_even_when_idle() -> None:
    for port in PROTECTED_PORTS:
        with pytest.raises(PortBusyError):
            assert_port_free(port)


def test_picked_port_is_never_protected_and_is_free() -> None:
    port = pick_free_port()
    assert port not in PROTECTED_PORTS
    assert_port_free(port)


def _serve(nonce: str) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps({"nonce": nonce}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_foreign_server_is_not_accepted() -> None:
    server = _serve("someone-else")
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/e2e/whoami"
        with pytest.raises(TimeoutError):
            wait_for_owned_server(url, "mine", sleeper, timeout=1)
    finally:
        sleeper.kill()
        sleeper.wait()
        server.shutdown()


def test_owned_server_is_accepted() -> None:
    server = _serve("mine")
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        wait_for_owned_server(f"http://127.0.0.1:{server.server_port}/x", "mine", sleeper, timeout=5)
    finally:
        sleeper.kill()
        sleeper.wait()
        server.shutdown()
