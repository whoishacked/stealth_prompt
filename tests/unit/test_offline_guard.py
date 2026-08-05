"""Tests for the guard that keeps the suite off the network.

The workbench legitimately binds loopback (a broker, a demo target), so the
guard permits 127.0.0.1 and refuses everything else. What matters is that no
test can reach an authorized target, a model provider, or a paid API.
"""

from __future__ import annotations

import socket

import pytest

from tests.conftest import NetworkAccessAttempted


class TestExternalIsBlocked:
    @pytest.mark.parametrize(
        "host", ["93.184.216.34", "10.0.0.5", "192.168.1.9", "8.8.8.8"]
    )
    def test_direct_connect_to_a_remote_address_is_blocked(self, host: str) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(NetworkAccessAttempted):
                sock.connect((host, 443))
        finally:
            sock.close()

    def test_create_connection_to_a_remote_address_is_blocked(self) -> None:
        with pytest.raises(NetworkAccessAttempted):
            socket.create_connection(("example.com", 443), timeout=0.01)

    def test_requests_cannot_reach_an_external_provider(self) -> None:
        requests = pytest.importorskip("requests")

        with pytest.raises(Exception) as excinfo:
            requests.post("https://api.openai.com/v1/chat/completions", timeout=0.01)

        chain: list[BaseException] = []
        current: BaseException | None = excinfo.value
        while current is not None and current not in chain:
            chain.append(current)
            current = current.__cause__ or current.__context__

        assert any(isinstance(exc, NetworkAccessAttempted) for exc in chain), (
            f"requests was not stopped by the offline guard: {excinfo.value!r}"
        )


class TestLoopbackIsAllowed:
    def test_loopback_connect_reaches_the_real_stack(self) -> None:
        # A closed loopback port must produce a connection error, not a guard
        # error: that proves the call went through rather than being refused.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            with pytest.raises(OSError) as excinfo:
                sock.connect(("127.0.0.1", 9))
            assert not isinstance(excinfo.value, NetworkAccessAttempted)
        finally:
            sock.close()

    def test_a_real_loopback_server_is_reachable(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(1.0)
            client.connect(("127.0.0.1", port))
            conn, _ = server.accept()
            conn.close()
        finally:
            client.close()
            server.close()
