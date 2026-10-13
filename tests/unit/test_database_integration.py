"""Integration tests for the database layer, driven through the real SQLAlchemy API.

Nothing here is patched or mocked. That is the point of the module: the unit
tests in test_database.py assert behaviour through MagicMock chains, and a mock
accepts any attribute name and any keyword, so a query call renamed to something
that does not exist, or filtered on a column that does not exist, stays green.
A live session cannot fake that, so these tests pin the API that src/database.py
actually uses.

Two tiers share one set of contract helpers:

  sqlite   In-memory, needs nothing from the environment, always runs.
  qa_db    The postgres QA database the repo already supports. Opt in with
           USE_QA_DB=1 (or SKIP_DB_TEST=1, the same switch get_database_url
           reads). With no reachable server the tier skips itself instead of
           failing, so a machine without postgres keeps the suite green.

Run:
    python -m pytest tests/unit/test_database_integration.py
    USE_QA_DB=1 python -m pytest tests/unit/test_database_integration.py
"""
import datetime
import json
import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from src.database import (
    INTERVAL_DURATIONS,
    DatabaseManager,
    MarketDataModel,
    ModelWeightsModel,
    get_database_url,
    now_utc,
)

SQLITE_URL = "sqlite:///:memory:"

# Rows this module writes into a shared QA database carry this symbol so they
# can be deleted on cleanup without touching real market data.
SENTINEL_SYMBOL = "ZZQAINTEG"


def fresh_db(db_url: str = SQLITE_URL) -> DatabaseManager:
    """Build a DatabaseManager with its tables created."""
    db = DatabaseManager(db_url=db_url)
    db.connection.init_db()
    return db


def _session(db: DatabaseManager) -> Session:
    """A real session; raises when the backend cannot provide one."""
    session = db.connection.get_session()
    if session is None:
        raise AssertionError("the database backend returned no session")
    return session


def contract_model_weights_round_trip(db: DatabaseManager, symbol: str) -> None:
    """Weights written as JSON must come back as the same dict."""
    weights = {"w1": 0.8, "w2": 0.2, "w3": 0.5}
    session = _session(db)
    session.add(
        ModelWeightsModel(
            symbol=symbol,
            last_trained=datetime.datetime.now(datetime.UTC),
            weights_json=json.dumps(weights),
        )
    )
    session.commit()
    session.close()

    retrieved = db.get_model_weights(symbol)
    if retrieved != weights:
        raise AssertionError(
            f"get_model_weights({symbol!r}) returned {retrieved!r}, "
            f"expected {weights!r}"
        )


def contract_model_weights_malformed_json(db: DatabaseManager, symbol: str) -> None:
    """Malformed weights_json must yield None rather than raising."""
    session = _session(db)
    session.add(
        ModelWeightsModel(
            symbol=symbol,
            last_trained=datetime.datetime.now(datetime.UTC),
            weights_json="invalid json {{",
        )
    )
    session.commit()
    session.close()

    result = db.get_model_weights(symbol)
    if result is not None:
        raise AssertionError(
            f"get_model_weights({symbol!r}) returned {result!r} for malformed JSON"
        )


def contract_model_weights_unknown_symbol(db: DatabaseManager) -> None:
    """An absent symbol must yield None."""
    result = db.get_model_weights("NO_SUCH_SYMBOL_ANYWHERE")
    if result is not None:
        raise AssertionError(f"get_model_weights for an unknown symbol returned {result!r}")


def contract_model_weights_writer(db: DatabaseManager, symbol: str) -> None:
    """save_model_weights must write the record get_model_weights reads back.

    A second write for the same key updates rather than duplicates, because the
    table's key column is unique and a training pass runs more than once.
    """
    weights = {"weights": [0.25, -0.5, 0.0, 1.0], "bias": 0.125}
    if not db.save_model_weights(symbol, weights):
        raise AssertionError("save_model_weights reported no session to write to")
    stored = db.get_model_weights(symbol)
    if stored != weights:
        raise AssertionError(
            f"save_model_weights({symbol!r}, {weights!r}) read back as {stored!r}"
        )

    updated = {"weights": [1.0], "bias": 0.0}
    db.save_model_weights(symbol, updated)
    rows = _session(db).query(ModelWeightsModel).filter_by(symbol=symbol).all()
    if len(rows) != 1:
        raise AssertionError(f"a second write left {len(rows)} records for one key")
    if db.get_model_weights(symbol) != updated:
        raise AssertionError(f"the second write left {db.get_model_weights(symbol)!r}, expected {updated!r}")


class _BlindQuery:
    """A Query that reports no record for the key.

    This is what a process sees when the other one committed between its check
    and its insert: the check comes back empty, and the insert that follows loses
    the table's UNIQUE constraint.
    """

    def filter_by(self, **columns):
        return self

    def first(self):
        return None


class _RaceSession:
    """A session whose first existence check comes back empty.

    The interleaving two processes reach is what is being arranged here; the
    session it wraps is real, so the constraint that refuses the insert and the
    rollback that recovers from it are the real ones.
    """

    def __init__(self, real):
        self.real = real
        self.blind = True

    def query(self, model):
        if self.blind:
            self.blind = False
            return _BlindQuery()
        return self.real.query(model)

    def add(self, record):
        return self.real.add(record)

    def commit(self):
        return self.real.commit()

    def rollback(self):
        return self.real.rollback()

    def close(self):
        return self.real.close()


def contract_save_model_weights_race(db: DatabaseManager, symbol: str) -> None:
    """A losing insert is recovered, so a training pass does not crash.

    Two processes training the same key both look for a record, both find
    nothing, and both insert; the table's UNIQUE constraint on the key is what
    makes one of them lose. What has to be true afterwards is one record for the
    key holding the weights of the pass that lost the race, not an exception out
    of the pass that was counting on the write.

    The interleaving is what is arranged here; the constraint and the recovery
    are the real ones. A session cannot be made stale by hand, so the first
    existence check is handed back empty the way a process sees the key when the
    other one committed between its check and its insert.
    """
    weights = {"weights": [0.5, -0.25], "bias": 0.75}

    # The record the other process already committed.
    winner = _session(db)
    try:
        winner.add(
            ModelWeightsModel(
                symbol=symbol,
                weights_json=json.dumps({"weights": [], "bias": 0.0}),
            )
        )
        winner.commit()
    finally:
        winner.close()

    real_get_session = db.connection.get_session

    def hand_over_one_race_session():
        return _RaceSession(real_get_session())

    with patch.object(db.connection, "get_session", side_effect=hand_over_one_race_session):
        if not db.save_model_weights(symbol, weights):
            raise AssertionError("save_model_weights reported no session to write to")

    rows = _session(db).query(ModelWeightsModel).filter_by(symbol=symbol).all()
    if len(rows) != 1:
        raise AssertionError(f"the losing insert left {len(rows)} records for one key")
    if db.get_model_weights(symbol) != weights:
        raise AssertionError(
            f"the losing insert left {db.get_model_weights(symbol)!r}, expected {weights!r}"
        )


def contract_query_api_rejects_unknown_column(db: DatabaseManager) -> None:
    """The filter API get_model_weights relies on must be real and must validate columns.

    get_model_weights filters with Query.filter_by(symbol=...). filter_by exists
    only on the real Query object, and it resolves keyword names against the
    model's columns, so a typoed column name raises instead of quietly matching
    every row. Neither property is observable through a MagicMock, which is why
    this check lives here rather than in the mocked unit tests.
    """
    session = _session(db)
    try:
        query = session.query(ModelWeightsModel)
        if not hasattr(query, "filter_by"):
            raise AssertionError(
                "session.query(ModelWeightsModel) has no filter_by; "
                "get_model_weights cannot work against this SQLAlchemy release"
            )
        try:
            query.filter_by(not_a_column=1).all()
        except InvalidRequestError:
            pass
        else:
            raise AssertionError(
                "filter_by(not_a_column=1) did not raise InvalidRequestError; "
                "a typoed column name would silently pass the suite"
            )
    finally:
        db.connection.close(session)


def contract_market_data_batch(db: DatabaseManager, symbol: str) -> None:
    """A batched write must be readable back through the query API."""
    bars: list[dict] = [
        {
            "symbol": symbol,
            "timestamp": datetime.datetime(2026, 10, 9, 13, 30, tzinfo=datetime.UTC),
            "open": 101.0,
            "high": 102.0,
            "low": 100.0,
            "close": 101.5,
            "volume": 10.0,
        },
        {
            "symbol": symbol,
            "timestamp": datetime.datetime(2026, 10, 9, 14, 0, tzinfo=datetime.UTC),
            "open": 102.0,
            "high": 103.0,
            "low": 101.0,
            "close": 102.5,
            "volume": 11.0,
        },
    ]
    db.add_market_data(bars)

    rows = db.get_historical_market_data(symbol)
    if len(rows) != 2:
        raise AssertionError(f"get_historical_market_data({symbol!r}) returned {len(rows)} rows, expected 2")
    if [r["close"] for r in rows] != [101.5, 102.5]:
        raise AssertionError(f"rows came back out of order or with wrong closes: {rows}")

    price = db.get_price_at_timestamp(symbol, bars[0]["timestamp"])
    if price != 101.5:
        raise AssertionError(f"get_price_at_timestamp returned {price!r}, expected 101.5")

    # The window is anchored to the latest bar, so the 13:30 bar opens it.
    for duration in ("1h", "24h", "ytd"):
        interval = db.get_price_at_interval_start(symbol, duration)
        if not isinstance(interval, float):
            raise AssertionError(
                f"get_price_at_interval_start({symbol!r}, {duration!r}) returned {interval!r}, "
                "expected a float"
            )
        if interval != 101.0:
            raise AssertionError(
                f"get_price_at_interval_start({symbol!r}, {duration!r}) returned {interval!r}, "
                "expected 101.0 (the 13:30 UTC bar)"
            )


def contract_extra_columns_are_dropped(db: DatabaseManager, symbol: str) -> None:
    """Keys outside MARKET_DATA_COLUMNS must be ignored, not rejected."""
    db.add_market_data(
        [
            {
                "symbol": symbol,
                "timestamp": datetime.datetime(2026, 10, 9, 13, 30),
                "open": 101.0,
                "high": 102.0,
                "low": 100.0,
                "close": 101.5,
                "volume": 10.0,
                "this_is_not_a_column": "ignored",
            }
        ]
    )
    rows = db.get_historical_market_data(symbol)
    if len(rows) != 1:
        raise AssertionError(
            f"record with an unknown key was not inserted: got {len(rows)} rows, expected 1"
        )


def contract_duplicate_bars_are_skipped(db: DatabaseManager, symbol: str) -> None:
    """A bar handed over twice is stored once, in batch and across batches.

    A stream reconnect or a re-run of the ingestion pipeline hands the same
    minute bar back, and ``add_market_data`` now checks what it is holding
    against what is already stored with one query per batch. Storing the bar
    twice would pollute every delta and volume total downstream.
    """
    bar = {
        "symbol": symbol,
        "timestamp": datetime.datetime(2026, 10, 9, 13, 30, tzinfo=datetime.UTC),
        "open": 101.0,
        "high": 102.0,
        "low": 100.0,
        "close": 101.5,
        "volume": 10.0,
    }
    db.add_market_data([bar])
    # The same bar twice inside one batch, then again in a later batch.
    db.add_market_data([dict(bar), dict(bar)])
    db.add_market_data([dict(bar)])
    rows = db.get_historical_market_data(symbol)
    if len(rows) != 1:
        raise AssertionError(
            f"duplicate bars stored more than once: got {len(rows)} rows, expected 1"
        )


def _interval_bars(symbol: str) -> list[dict]:
    """Bars spread across every dashboard duration's window, relative to now.

    ``open`` encodes how far back the bar sits, so an interval read that lands
    on the wrong bar is visible in the number it returns.
    """
    now = now_utc()
    return [
        {
            "symbol": symbol,
            "timestamp": now - datetime.timedelta(hours=hours),
            "open": float(100 + hours),
            "high": float(101 + hours),
            "low": float(99 + hours),
            "close": float(100.5 + hours),
            "volume": 1000.0,
        }
        for hours in (0, 2, 6, 25, 40, 24 * 31, 24 * 400)
    ]


def contract_interval_batching_matches_single_lookups(
    db: DatabaseManager, symbol: str
) -> None:
    """Reading every duration in one call returns what per-duration reads return.

    The dashboard used to ask for each duration separately, which paid its own
    anchor read every time. ``get_prices_at_interval_starts`` shares one anchor
    read across them, and this is the contract that batching must not change
    which bar a duration lands on.
    """
    db.add_market_data(_interval_bars(symbol))
    batched = db.get_prices_at_interval_starts(symbol)
    if set(batched) != set(INTERVAL_DURATIONS):
        raise AssertionError(
            f"batched read returned durations {sorted(batched)}, "
            f"expected {sorted(INTERVAL_DURATIONS)}"
        )

    seeded = {float(bar["open"]) for bar in _interval_bars(symbol)}
    # A write that stores nothing still clears the interval cache, so the
    # single-duration lookups below recompute from the database rather than
    # reusing whatever the batched call just cached.
    db.add_market_data(_interval_bars(symbol)[:1])
    for duration in INTERVAL_DURATIONS:
        value = batched[duration]
        if value is None or value not in seeded:
            raise AssertionError(f"{duration} read {value!r}, not one of the seeded bars")
        single = db.get_price_at_interval_start(symbol, duration)
        if single != value:
            raise AssertionError(
                f"{duration}: batched read {value}, single-duration read {single}"
            )

    # A wider window can only reach an older bar, and older bars sit lower in
    # this seeding, so the values must not decrease as the duration grows.
    windows = ["1h", "24h", "5d", "30d", "1y"]
    ordered = [batched[duration] for duration in windows]
    if ordered != sorted(ordered):
        raise AssertionError(f"wider windows read newer bars: {ordered}")


def contract_writes_invalidate_interval_cache(db: DatabaseManager, symbol: str) -> None:
    """A bar stored between two interval reads is visible to the second one.

    Interval-start prices are cached for the minute they were read in, which is
    only sound if a write drops them: the dashboard would otherwise keep showing
    yesterday's anchor for the rest of the minute.
    """
    db.add_market_data(_interval_bars(symbol))
    before = db.get_prices_at_interval_starts(symbol, ["24h"])["24h"]
    if before is None:
        raise AssertionError("no 24h start price for seeded bars")

    # A bar just inside the 24h window becomes the oldest bar in it.
    new_bar = {
        "symbol": symbol,
        "timestamp": now_utc() - datetime.timedelta(hours=23),
        "open": 999.0,
        "high": 1000.0,
        "low": 998.0,
        "close": 999.5,
        "volume": 1000.0,
    }
    db.add_market_data([new_bar])
    after = db.get_prices_at_interval_starts(symbol, ["24h"])["24h"]
    if after != 999.0:
        raise AssertionError(
            f"24h start price still {after} after storing a 23h bar; "
            "the interval cache was not invalidated on write"
        )


# Opt in with USE_QA_DB / SKIP_DB_TEST, the same switches get_database_url reads.
# The connection attempt only happens once opted in, so importing this module
# never reaches out to a network.
_QA_OPT_IN = bool(os.getenv("USE_QA_DB") or os.getenv("SKIP_DB_TEST"))


def _qa_db_reachable() -> bool:
    """True only when the selected postgres backend can actually be connected to."""
    url = get_database_url()
    if "sqlite" in url:
        return False
    try:
        engine = create_engine(url, connect_args={"connect_timeout": 1})
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        # A missing psycopg driver, a refused connection, or an unreadable
        # database all mean the same thing here: this tier cannot run.
        return False


QA_DB_AVAILABLE = _QA_OPT_IN and _qa_db_reachable()


class TestSqliteContract(unittest.TestCase):
    """The contract asserted against a live in-memory SQLAlchemy session."""

    def setUp(self):
        self.db = fresh_db(SQLITE_URL)
        self.symbol = "AAPL"

    def test_model_weights_round_trip(self):
        contract_model_weights_round_trip(self.db, self.symbol)

    def test_model_weights_malformed_json_yields_none(self):
        contract_model_weights_malformed_json(self.db, self.symbol)

    def test_model_weights_unknown_symbol_yields_none(self):
        contract_model_weights_unknown_symbol(self.db)

    def test_save_model_weights_writes_and_updates(self):
        contract_model_weights_writer(self.db, self.symbol)

    def test_save_model_weights_recovers_a_losing_insert(self):
        contract_save_model_weights_race(self.db, self.symbol)

    def test_query_api_rejects_unknown_column(self):
        contract_query_api_rejects_unknown_column(self.db)

    def test_market_data_batch_round_trip(self):
        contract_market_data_batch(self.db, self.symbol)

    def test_extra_columns_are_dropped(self):
        contract_extra_columns_are_dropped(self.db, self.symbol)

    def test_duplicate_bars_are_skipped(self):
        contract_duplicate_bars_are_skipped(self.db, self.symbol)

    def test_interval_batching_matches_single_lookups(self):
        contract_interval_batching_matches_single_lookups(self.db, self.symbol)

    def test_writes_invalidate_interval_cache(self):
        contract_writes_invalidate_interval_cache(self.db, self.symbol)


class TestQaDatabaseContract(unittest.TestCase):
    """The same contract against the real postgres QA database."""

    @unittest.skipUnless(
        QA_DB_AVAILABLE,
        "set USE_QA_DB=1 (or SKIP_DB_TEST=1) and have a reachable postgres instance",
    )
    def test_contract_holds_against_postgres(self):
        db = fresh_db(get_database_url())
        try:
            contract_model_weights_round_trip(db, SENTINEL_SYMBOL)
            contract_model_weights_unknown_symbol(db)
            contract_model_weights_writer(db, SENTINEL_SYMBOL + "W")
            contract_query_api_rejects_unknown_column(db)
            contract_market_data_batch(db, SENTINEL_SYMBOL)
            # Each market-data contract counts its own rows, so each gets a
            # symbol of its own inside the sentinel namespace cleanup wipes.
            contract_duplicate_bars_are_skipped(db, SENTINEL_SYMBOL + "DUP")
            contract_interval_batching_matches_single_lookups(db, SENTINEL_SYMBOL + "INT")
            contract_writes_invalidate_interval_cache(db, SENTINEL_SYMBOL + "CACHE")
        finally:
            self._cleanup(db)

    def _cleanup(self, db: DatabaseManager) -> None:
        """Delete only the sentinel rows this tier wrote."""
        session = _session(db)
        try:
            session.query(ModelWeightsModel).filter(
                ModelWeightsModel.symbol.like(f"{SENTINEL_SYMBOL}%")
            ).delete()
            session.query(MarketDataModel).filter(
                MarketDataModel.symbol.like(f"{SENTINEL_SYMBOL}%")
            ).delete()
            session.commit()
        finally:
            db.connection.close(session)


if __name__ == "__main__":
    unittest.main()
