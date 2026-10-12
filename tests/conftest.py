"""Pytest configuration shared by the whole test tree.

Provides the asyncio event loop fixture and a reachability gate for the
modules that drive a live web server against a live database. Those modules
are integration tests, not unit tests: without a reachable database the
FastAPI handlers block on socket connects (the configured host resolves to
several addresses, so psycopg2 retries each one), which stalls the suite for
minutes. Skipping them is the honest outcome.

Unit tests that reach the same database by accident are handled the other way:
the database is declared unavailable once, so their documented fallback paths
run fast and deterministically.
"""

import socket
import time
import pytest
import asyncio

from functools import lru_cache

from src.database import get_database_url


def _sqlite_is_usable(url: str) -> bool:
    """Return True when a sqlite engine can actually be created and used."""
    from sqlalchemy import create_engine

    try:
        engine = create_engine(url)
        with engine.connect():
            pass
    except Exception:
        return False
    return True


def _database_reachable(per_address_timeout: float = 1.0, deadline: float = 3.0) -> bool:
    """Probe the database the application would use, without blocking.

    A raw socket connect stands in for psycopg2 because psycopg2 retries
    every address the host resolves to and pays a connect timeout per
    address: the configured host resolves through macOS name resolution to
    a dozen addresses, so one psycopg2 connect costs ~6 seconds and every
    request handler repeats that cost.
    """
    url = get_database_url()
    if url.startswith("sqlite"):
        return _sqlite_is_usable(url)

    try:
        tail = url.split("://", 1)[1].split("@")[-1].split("?")[0].split("/")[0]
        host, port_str = tail.rsplit(":", 1)
        port = int(port_str)
    except (IndexError, ValueError):
        return False

    try:
        resolved = socket.getaddrinfo(host, port)
    except OSError:
        return False

    # Name resolution reports the same socket address many times.
    candidates = {(family, sockaddr) for family, _, _, _, sockaddr in resolved}

    started = time.monotonic()
    for family, sockaddr in candidates:
        if time.monotonic() - started > deadline:
            return False
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(per_address_timeout)
                probe.connect(sockaddr)
        except (OSError, OverflowError):
            continue
        return True
    return False


@lru_cache(maxsize=1)
def _db_available() -> bool:
    """Cached answer to "can the application reach its database?"."""
    return _database_reachable()


@pytest.fixture(scope="session")
def live_database():
    """Session-scoped gate: skip any test that needs a reachable database."""
    if not _db_available():
        pytest.skip(
            "database behind DATABASE_URL is not reachable; "
            "these are live-server integration tests"
        )


@pytest.fixture(scope="session", autouse=True)
def unavailable_database_is_explicit():
    """Make "the database is unavailable" a fast, explicit state for unit tests.

    ``src.web_server`` reaches the database through ``get_db_manager()``, a
    module level singleton that connects lazily. Unit tests of the tick buffers
    and of ``compute_recommendation`` never intend to query that database, yet
    they pay a real psycopg2 connect every time they touch it: the configured
    host resolves to several addresses, so psycopg2 retries each one and a
    single call costs seconds. When the database is unreachable we hand back
    ``None``, which is the state the application already reaches when
    ``DatabaseManager()`` fails to construct, so the fallback paths those tests
    describe run deterministically instead of by accident.
    """
    if _db_available():
        return
    try:
        import src.web_server
    except ImportError:  # the web server pulls in an optional dependency stack
        return
    src.web_server.get_db_manager = lambda: None


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
