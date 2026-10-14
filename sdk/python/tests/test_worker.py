import asyncio
import os
import signal
import socket
import sys

import pytest
from agentworkflows import worker


@pytest.mark.parametrize(("value", "address"), [
    (None, ("0.0.0.0", 8081)), ("127.0.0.1:9000", ("127.0.0.1", 9000)), ("[::1]:8081", ("::1", 8081)),
    (":8082", ("0.0.0.0", 8082)), ("off", None), ("OFF", None), ("", None),
])
def test_health_address_defaults_to_the_chart_port_and_can_move_or_turn_off(monkeypatch, value, address):
    if value is None:
        monkeypatch.delenv("AGENTWORKFLOWS_HEALTH_ADDRESS", raising=False)
    else:
        monkeypatch.setenv("AGENTWORKFLOWS_HEALTH_ADDRESS", value)
    assert worker.health_address() == address


@pytest.mark.parametrize("value", ["8081", "localhost", "host:port", "127.0.0.1:70000", "127.0.0.1:-1"])
def test_invalid_health_address_is_actionable(monkeypatch, value):
    monkeypatch.setenv("AGENTWORKFLOWS_HEALTH_ADDRESS", value)
    with pytest.raises(ValueError, match="host:port"):
        worker.health_address()


class FakeWorker:
    is_running = True

    def __init__(self, client, **options):
        self.options = options

    async def __aenter__(self):
        # Simulate Ctrl+C once the worker is up.
        os.kill(os.getpid(), signal.SIGINT)
        return self

    async def __aexit__(self, *exc):
        return False


def fake_temporal(monkeypatch):
    async def connect(*args, **kwargs):
        return object()

    monkeypatch.setenv("AGENTWORKFLOWS_API_KEY", "worker-key")
    monkeypatch.setattr(worker.Client, "connect", connect)
    monkeypatch.setattr(worker, "Worker", FakeWorker)


def test_busy_health_port_names_the_setting_instead_of_temporal(monkeypatch):
    fake_temporal(monkeypatch)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        monkeypatch.setenv("AGENTWORKFLOWS_HEALTH_ADDRESS", f"127.0.0.1:{busy.getsockname()[1]}")
        with pytest.raises(SystemExit, match="AGENTWORKFLOWS_HEALTH_ADDRESS") as exit_info:
            worker.run_worker([])
    assert "TEMPORAL_ADDRESS" not in str(exit_info.value)


@pytest.mark.skipif(sys.platform == "win32", reason="Signal delivery to the running loop is POSIX-only")
def test_local_worker_can_run_without_a_health_server(monkeypatch):
    fake_temporal(monkeypatch)
    monkeypatch.setenv("AGENTWORKFLOWS_HEALTH_ADDRESS", "off")

    async def refuse(*args, **kwargs):
        raise AssertionError("health server started")

    monkeypatch.setattr(worker.asyncio, "start_server", refuse)
    worker.run_worker([])


@pytest.mark.skipif(sys.platform == "win32", reason="Signal delivery to the running loop is POSIX-only")
def test_health_server_answers_on_the_configured_address(monkeypatch):
    fake_temporal(monkeypatch)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("AGENTWORKFLOWS_HEALTH_ADDRESS", f"127.0.0.1:{port}")
    answers = []

    class CheckingWorker(FakeWorker):
        async def __aenter__(self):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /healthz HTTP/1.1\r\n\r\n")
            await writer.drain()
            answers.append(await reader.readline())
            writer.close()
            return await super().__aenter__()

    monkeypatch.setattr(worker, "Worker", CheckingWorker)
    worker.run_worker([])
    assert answers == [b"HTTP/1.1 200 OK\r\n"]
