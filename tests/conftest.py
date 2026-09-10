import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_environ():
    """Snapshot and restore os.environ around every test.

    Several code paths call load_dotenv() (tools/check_halo.py,
    src/main.py) unconditionally. load_dotenv() only fills in vars not
    already set, but whatever it does set persists in os.environ for the
    rest of the process -- monkeypatch.setenv's auto-revert only undoes
    keys *it* set, not keys load_dotenv() added directly. Without this,
    any test that exercises one of those load_dotenv() calls leaks the
    real local .env's values into every test that runs afterward in the
    same pytest process, regardless of file or test order.
    """
    original = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(original)
