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

from sqlalchemy import create_engine, text
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session
from typing import Optional

from src.database import (
    DatabaseManager,
    MarketDataModel,
    ModelWeightsModel,
    get_database_url,
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


def _session(db: DatabaseManager) -> Optional[Session]:
    """A real session, or None when the backend cannot provide one."""
    return db.connection.get_session()


def contract_model_weights_round_trip(db: DatabaseManager, symbol: str) -> None:
    """Weights written as JSON must come back as the same dict."""
    weights = {"w1": 0.8, "w2": 0.2, "w3": 0.5}
    session = _session(db)
    session.add(
        ModelWeightsModel(
            symbol=symbol,
            last_trained=datetime.datetime.now(),
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
            last_trained=datetime.datetime.now(),
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
    bars = [
        {
            "symbol": symbol,
            "timestamp": datetime.datetime(2026, 10, 9, 13, 30),
            "open": 101.0,
            "high": 102.0,
            "low": 100.0,
            "close": 101.5,
            "volume": 10.0,
        },
        {
            "symbol": symbol,
            "timestamp": datetime.datetime(2026, 10, 9, 14, 0),
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

    interval = db.get_price_at_interval_start(symbol, "1H")
    if not isinstance(interval, float):
        raise AssertionError(f"get_price_at_interval_start returned {interval!r}, expected a float")


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

    def test_query_api_rejects_unknown_column(self):
        contract_query_api_rejects_unknown_column(self.db)

    def test_market_data_batch_round_trip(self):
        contract_market_data_batch(self.db, self.symbol)

    def test_extra_columns_are_dropped(self):
        contract_extra_columns_are_dropped(self.db, self.symbol)


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
            contract_query_api_rejects_unknown_column(db)
            contract_market_data_batch(db, SENTINEL_SYMBOL)
        finally:
            self._cleanup(db)

    def _cleanup(self, db: DatabaseManager) -> None:
        """Delete only the sentinel rows this tier wrote."""
        session = _session(db)
        try:
            session.query(ModelWeightsModel).filter(
                ModelWeightsModel.symbol == SENTINEL_SYMBOL
            ).delete()
            session.query(MarketDataModel).filter(
                MarketDataModel.symbol == SENTINEL_SYMBOL
            ).delete()
            session.commit()
        finally:
            db.connection.close(session)


if __name__ == "__main__":
    unittest.main()
