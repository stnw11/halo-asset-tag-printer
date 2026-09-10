import socket

import pytest

from src.printer_client import PrinterConnectionError, dry_run_write, send_batch, send_zpl


def test_dry_run_write_creates_file_with_zpl_content(tmp_path):
    out = tmp_path / "nested" / "output.zpl"
    result_path = dry_run_write("^XA^XZ\n", str(out))
    assert result_path == out
    assert out.read_text() == "^XA^XZ\n"


def test_send_zpl_returns_byte_count(monkeypatch):
    sent = {}

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):
            pass

        def sendall(self, data):
            sent["data"] = data

    monkeypatch.setattr(socket, "create_connection", lambda addr, timeout=None: FakeSocket())
    n = send_zpl("^XA^XZ", "10.0.0.1", 9100)
    assert n == len("^XA^XZ".encode("utf-8"))
    assert sent["data"] == "^XA^XZ".encode("utf-8")


def test_send_zpl_connection_refused_raises_clear_error(monkeypatch):
    def raise_refused(addr, timeout=None):
        raise ConnectionRefusedError()

    monkeypatch.setattr(socket, "create_connection", raise_refused)
    with pytest.raises(PrinterConnectionError, match="Connection refused"):
        send_zpl("^XA^XZ", "10.0.0.1", 9100)


def test_send_zpl_timeout_raises_clear_error(monkeypatch):
    def raise_timeout(addr, timeout=None):
        raise socket.timeout()

    monkeypatch.setattr(socket, "create_connection", raise_timeout)
    with pytest.raises(PrinterConnectionError, match="Timed out"):
        send_zpl("^XA^XZ", "10.0.0.1", 9100, timeout=1.0)


def test_send_zpl_host_unreachable_raises_clear_error(monkeypatch):
    def raise_oserror(addr, timeout=None):
        raise OSError("No route to host")

    monkeypatch.setattr(socket, "create_connection", raise_oserror)
    with pytest.raises(PrinterConnectionError, match="Network error"):
        send_zpl("^XA^XZ", "10.0.0.1", 9100)


def test_send_batch_opens_one_connection_for_all_jobs(monkeypatch):
    connections = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def settimeout(self, t):
            pass

        def sendall(self, data):
            connections[-1]["data"] = data

    def fake_create_connection(addr, timeout=None):
        connections.append({})
        return FakeSocket()

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    n = send_batch(["^XA^FS^XZ", "^XA^FS^XZ", "^XA^FS^XZ"], "10.0.0.1", 9100)
    assert len(connections) == 1  # one socket, not three
    assert connections[0]["data"] == b"^XA^FS^XZ^XA^FS^XZ^XA^FS^XZ"
    assert n == len(b"^XA^FS^XZ^XA^FS^XZ^XA^FS^XZ")


def test_send_batch_retries_then_raises(monkeypatch):
    attempts = []

    def always_refuse(addr, timeout=None):
        attempts.append(1)
        raise ConnectionRefusedError()

    monkeypatch.setattr(socket, "create_connection", always_refuse)
    with pytest.raises(PrinterConnectionError):
        send_batch(["^XA^XZ"], "10.0.0.1", 9100, retries=2, retry_backoff_seconds=0)
    assert len(attempts) == 3  # initial attempt + 2 retries
