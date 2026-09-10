"""Raw-socket delivery of a ZPL byte stream to the printer's raw/JetDirect
port (9100), or a local-file dry-run for layout iteration without wasting
labels/ribbon.

No drivers, no CUPS, no print spooler -- this opens a plain TCP socket and
writes the ZPL bytes straight to the printer, which is how Brady's i-series
"Raw-IP" listener (and the equivalent Zebra/JetDirect convention) expects
to receive a print job on port 9100.

Ported from the brady-i4311-printer PoC's sender.py unchanged -- its
connection-refused, timeout, and OSError messages are already specific and
operator-useful. send_batch() is new: one socket, every job in the batch,
one close, since tagging a cart of assets should not open N connections.
"""
from __future__ import annotations

import socket
import time
from pathlib import Path


class PrinterConnectionError(Exception):
    """Raised for any failure connecting to or writing to the printer socket."""


def send_zpl(
    zpl: str,
    host: str,
    port: int,
    timeout: float = 5.0,
    encoding: str = "utf-8",
) -> int:
    """Open a TCP socket to host:port, send the ZPL payload, and close.

    Returns the number of bytes sent. Raises PrinterConnectionError with a
    clear, specific message on connection-refused, timeout, host-unreachable,
    or partial-send failures -- this is meant to run against a real printer
    on the LAN, so these are expected failure modes, not edge cases.
    """
    payload = zpl.encode(encoding)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(payload)
    except ConnectionRefusedError as exc:
        raise PrinterConnectionError(
            f"Connection refused by {host}:{port}. Printer may be powered "
            f"off, the raw-IP listener may be disabled, or {port} is the "
            f"wrong port for this unit."
        ) from exc
    except socket.timeout as exc:
        raise PrinterConnectionError(
            f"Timed out after {timeout}s connecting to or sending data to "
            f"{host}:{port}. Check network reachability and that the "
            f"printer isn't paused/out of media."
        ) from exc
    except OSError as exc:
        raise PrinterConnectionError(
            f"Network error reaching {host}:{port}: {exc}. Check that "
            f"{host} is the correct printer IP and is reachable from this "
            f"container/host (ping/traceroute, VLAN/firewall rules)."
        ) from exc
    return len(payload)


def send_batch(
    zpl_jobs: list[str],
    host: str,
    port: int,
    timeout: float = 5.0,
    retries: int = 0,
    retry_backoff_seconds: float = 1.0,
    encoding: str = "utf-8",
) -> int:
    """Send every job in zpl_jobs over a single TCP connection.

    Each job is its own ^XA...^XZ block; concatenating them in one stream
    is how ZPL is meant to be fed, and it means printing a cart of N assets
    opens one connection, not N. A successful send means the printer
    accepted the bytes, not that tags physically printed -- out of media,
    ribbon out, head open, and paused all still accept jobs.

    Retries the whole batch (not per-job) on connection failure, honoring
    `retries`, then raises PrinterConnectionError.
    """
    combined = "".join(zpl_jobs)
    attempt = 0
    while True:
        try:
            return send_zpl(combined, host, port, timeout=timeout, encoding=encoding)
        except PrinterConnectionError:
            if attempt >= retries:
                raise
            attempt += 1
            time.sleep(retry_backoff_seconds)


def dry_run_write(zpl: str, path: str, encoding: str = "utf-8") -> Path:
    """Write the generated ZPL to a local file instead of a socket, for
    fast layout iteration without spending labels/ribbon."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(zpl, encoding=encoding)
    return out_path
