"""Port safety for e2e: never touch the live pilot ports, and never trust a server we did not start."""

import json
import socket
import subprocess
import time
import urllib.request

PROTECTED_PORTS = frozenset({3100, 5174})


class PortBusyError(RuntimeError):
    pass


def assert_port_free(port: int) -> None:
    if port in PROTECTED_PORTS:
        raise PortBusyError(f"Port {port} is reserved for the live pilot")
    with socket.socket() as s:  # no SO_REUSEADDR: a listener, even a lingering one, must fail the bind
        try:
            s.bind(("127.0.0.1", port))
        except OSError as e:
            raise PortBusyError(f"Port {port} is already in use") from e


def wait_until_port_free(port: int, timeout: float = 90) -> None:
    """Fail on a busy port, but let the previous module's own sockets leave TIME_WAIT (about 30s on macOS)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            assert_port_free(port)
            return
        except PortBusyError:
            if port in PROTECTED_PORTS or time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def pick_free_port() -> int:
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port: int = s.getsockname()[1]
        if port not in PROTECTED_PORTS:
            return port


def wait_for_owned_server(
    url: str, nonce: str, process: subprocess.Popen[bytes], timeout: float = 60
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Server for {url} exited with {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if json.load(response).get("nonce") == nonce:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    raise TimeoutError(f"{url} did not answer with this run's nonce")
