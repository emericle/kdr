"""Database management module for market data and model weights."""
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import DBAPIError, IntegrityError, InvalidRequestError, NoInspectionAvailable
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import QueuePool

from src.times import epoch_seconds, hhmmss, minute_key, now_utc, to_utc

logger = logging.getLogger(__name__)

# What a failed database operation can raise. SQLAlchemy wraps every DBAPI
# failure (connection lost, rejected insert, statement refused) under
# DBAPIError, and InvalidRequestError covers a query built against something the
# schema does not have. Readers answer these with an empty result, so a lost
# database cannot stop the pipeline; anything else is a bug in this file and is
# left to reach the caller.
DB_FAILURES = (DBAPIError, InvalidRequestError)

_dotenv_loaded = False


def load_environment() -> None:
    """Read ``.env`` into the environment, once, on first use.

    Nothing in this module loads it at import any more: importing a module is
    not an action, and a process that imported ``src.database`` for its models
    used to pick up credentials as a side effect. Callers that read
    ``os.getenv`` directly (the gatekeeper, the stream client) call this first.
    """
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    load_dotenv()
    _dotenv_loaded = True


def get_database_url(override_url: str | None = None) -> str:
    """Resolve the database URL from configuration only.

    Priority: explicit override, then ``DATABASE_URL``, then ``QA_DATABASE_URL``
    (the test/QA database, used when tests opt in via ``USE_QA_DB``). Every value
    comes from the environment or ``.env``; nothing is hardcoded, so a live
    password can no longer be committed to this file.

    Returns an empty string when nothing is configured, which makes
    :class:`DatabaseConnection` report the problem and hand back ``None`` rather
    than silently operating on someone else's database.
    """
    if override_url:
        return override_url
    load_environment()
    qa_url = os.getenv("QA_DATABASE_URL", "")
    if os.getenv("SKIP_DB_TEST") or os.getenv("USE_QA_DB"):
        if not qa_url:
            logger.error(
                "QA_DATABASE_URL is not set; database operations are disabled. "
                "Add it to .env to use the QA database."
            )
        return qa_url
    env_url = os.getenv("DATABASE_URL", "")
    if not env_url:
        logger.info(
            "No DATABASE_URL found in the environment or .env. Set DATABASE_URL "
            "to enable database operations (or QA_DATABASE_URL for the test database)."
        )
    return env_url


class DatabaseConnection:
    """Handles the underlying SQL connection and session factory."""
    def __init__(self, db_url: str | None = None):
        self.db_url = get_database_url(db_url)
        if "sqlite" in self.db_url:
            self.engine = create_engine(self.db_url)
        else:
            self.engine = create_engine(
                self.db_url,
                poolclass=QueuePool,
                pool_pre_ping=True,
                pool_recycle=300,
                connect_args={"connect_timeout": 1}
            )

        self.session_factory = sessionmaker(bind=self.engine)

    def get_session(self):
        """Returns a new SQLAlchemy session."""
        if self.engine:
            return self.session_factory()
        return None

    def init_db(self) -> bool:
        """Bootstrap a genuinely empty database, or verify an existing schema.

        ``Base.metadata.create_all`` runs only when none of the application's
        tables exist yet (first run). Once tables exist, schema changes go
        through versioned Alembic migrations (``alembic upgrade head`` /
        ``./bin/upgrade.sh``), never through ``create_all`` and never through a
        drop. When existing tables differ from the model schema, the mismatch is
        logged and ``False`` is returned rather than silently doing nothing.
        """
        if not self.engine:
            return False
        try:
            existing = _existing_model_tables(self.engine)
            if not existing:
                logger.info("Initialising database tables on empty database...")
                Base.metadata.create_all(self.engine)
                _stamp_alembic_head(self.engine)
                logger.info("Database initialized successfully.")
                return True

            mismatches = schema_mismatches(self.engine)
            if mismatches:
                logger.warning(
                    "Database schema mismatch detected (%s). Run 'alembic upgrade head' "
                    "(or ./bin/upgrade.sh) to migrate in place without losing data.",
                    "; ".join(mismatches),
                )
                return False

            logger.info("Existing database schema verified.")
            return True
        except DB_FAILURES as e:
            logger.warning(f"Notice: Database initialization skipped: {e}")
            return False

    def close(self, session):
        """Closes an active session."""
        if session:
            session.close()


def _existing_model_tables(engine: Any) -> set[str]:
    """Model table names that already exist in ``engine``."""
    try:
        inspector = sa_inspect(engine)
    except (NoInspectionAvailable, AttributeError, TypeError):
        return set()
    return set(inspector.get_table_names()) & set(Base.metadata.tables)


def schema_mismatches(engine: Any) -> list[str]:
    """Differences between ``Base.metadata`` and the tables stored in ``engine``.

    Returns an empty list when every model table, column, and named index is
    present in the database.
    """
    try:
        inspector = sa_inspect(engine)
    except (NoInspectionAvailable, AttributeError, TypeError):
        return []
    present_tables = set(inspector.get_table_names())
    mismatches: list[str] = []
    for table_name in sorted(Base.metadata.tables):
        if table_name not in present_tables:
            mismatches.append(f"missing table '{table_name}'")
            continue
        table_obj = Base.metadata.tables[table_name]
        stored_cols = {col["name"] for col in inspector.get_columns(table_name)}
        expected_cols = {col.name for col in table_obj.columns}
        missing_cols = sorted(expected_cols - stored_cols)
        if missing_cols:
            mismatches.append(
                f"table '{table_name}' is missing columns: {', '.join(missing_cols)}"
            )
        stored_indexes = {
            idx["name"] for idx in inspector.get_indexes(table_name) if idx.get("name")
        }
        expected_indexes = {idx.name for idx in table_obj.indexes if idx.name}
        missing_indexes = sorted(expected_indexes - stored_indexes)
        if missing_indexes:
            mismatches.append(
                f"table '{table_name}' is missing indexes: {', '.join(missing_indexes)}"
            )
    return mismatches


def current_alembic_revision(engine: Any) -> str | None:
    """Return the revision recorded in ``alembic_version``, or ``None`` if unversioned."""
    try:
        inspector = sa_inspect(engine)
        if "alembic_version" not in set(inspector.get_table_names()):
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
            return str(row[0]) if row and row[0] else None
    except (*DB_FAILURES, NoInspectionAvailable, AttributeError, TypeError):
        return None


def _alembic_config() -> Any:
    """Build an Alembic ``Config`` pointed at this repository's ``alembic/`` directory."""
    from alembic.config import Config

    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    return cfg


def _stamp_alembic_head(engine: Any) -> None:
    """Record the current Alembic head revision after bootstrapping an empty database."""
    try:
        sa_inspect(engine)
    except (NoInspectionAvailable, AttributeError, TypeError):
        return
    try:
        from alembic import command

        cfg = _alembic_config()
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.stamp(cfg, "head")
    except (*DB_FAILURES, NoInspectionAvailable, AttributeError, TypeError):
        pass


def upgrade_db(
    db_url: str | None = None,
    engine: Any | None = None,
    revision: str = "head",
) -> str | None:
    """Run Alembic migrations up to ``revision`` in place without dropping data.

    Accepts either a live SQLAlchemy ``engine`` (so in-memory SQLite tests
    migrate the same connection that holds their seeded bars) or a database URL
    resolved through :func:`get_database_url`.
    """
    from alembic import command

    cfg = _alembic_config()

    if engine is not None:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, revision)
        return current_alembic_revision(engine)

    resolved_url = get_database_url(db_url)
    if not resolved_url:
        raise RuntimeError("No database URL configured for upgrade_db.")
    cfg.set_main_option("sqlalchemy.url", resolved_url)
    command.upgrade(cfg, revision)
    target_engine = create_engine(resolved_url)
    try:
        return current_alembic_revision(target_engine)
    finally:
        target_engine.dispose()

# Base Model for SQLAlchemy 2.0+
class Base(DeclarativeBase):
    pass

class MarketDataModel(Base):
    """Schema for market data records.

    ``(symbol, timestamp)`` carries a unique index: a stream reconnect or a
    re-run hands the same minute bar back, and a duplicate bar pollutes every
    delta and volume calculation downstream. ``add_market_data`` skips such
    records instead of failing the batch.
    """
    __tablename__ = 'market_data'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    timestamp = Column(DateTime(timezone=True), index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    bar_key_index = Index("market_data_bar_unique", symbol, timestamp, unique=True)


def bar_key(item: dict[str, Any]) -> tuple | None:
    """The uniqueness key of a market data record, or None when it has no timestamp.

    The timestamp is normalized with :func:`src.times.to_utc` so a bar written
    from a ``datetime`` and the same bar read back from the database produce the
    same key.
    """
    symbol = item.get("symbol")
    timestamp = item.get("timestamp")
    if symbol is None or timestamp is None:
        return None
    try:
        return (str(symbol).upper(), to_utc(timestamp))
    except (TypeError, ValueError):
        return None

class SentimentDataModel(Base):
    """Schema for sentiment data records."""
    __tablename__ = 'sentiment_data'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    timestamp = Column(DateTime(timezone=True))
    score = Column(Float)
    source = Column(String)

class ModelWeightsModel(Base):
    """Schema for model weights records."""
    __tablename__ = 'model_weights'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True)
    last_trained = Column(DateTime(timezone=True))
    weights_json = Column(Text)

MARKET_DATA_COLUMNS = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}

INTERVAL_DURATIONS = ("1h", "24h", "5d", "30d", "1y", "ytd")
INTERVAL_OFFSETS = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "5d": timedelta(days=5),
    "30d": timedelta(days=30),
    "1y": timedelta(days=365),
}


def interval_window(duration: str, now: datetime, latest_time: datetime) -> tuple:
    """The ``(target_time, ref_end_time)`` a duration's start price is read from.

    When the markets have been closed longer than the duration -- a weekend,
    after hours -- the interval anchors to the latest available open session
    instead of to wall-clock time, so the delta the dashboard shows stays
    meaningful. An unrecognized duration is treated as 24h from now, which is
    what the single-duration lookup has always done.
    """
    offset = INTERVAL_OFFSETS.get(duration)
    if offset is None:
        if duration == "ytd":
            base_year = latest_time.year if (now - latest_time) > timedelta(days=365) else now.year
            return datetime(base_year, 1, 1, tzinfo=UTC), now
        return now - INTERVAL_OFFSETS["24h"], now
    ref_end_time = latest_time if (now - latest_time) > offset else now
    return ref_end_time - offset, ref_end_time


class DatabaseManager:
    """High-level manager for database operations."""
    def __init__(self, db_url: str | None = None):
        self.connection = DatabaseConnection(db_url)
        # Interval-start prices are read several times per dashboard refresh;
        # they are kept for the minute they were read in and dropped on write.
        self._interval_cache: dict[tuple, float | None] = {}
        self._interval_cache_minute: str | None = None

    def _rollback(self, session) -> None:
        """Roll a failed transaction back, ignoring a rollback that itself fails."""
        try:
            session.rollback()
        except DB_FAILURES:
            pass

    def _prepare_bars(self, data: list[dict]) -> tuple[list[tuple], list[tuple]]:
        """Drop keys the model does not have and remove bars repeated within ``data``.

        Returns ``(records, keys)``: one ``(bar_key, MarketDataModel)`` pair per
        bar worth writing, and the keys of those bars that have one, in the order
        they first appear.
        """
        records: list[tuple] = []
        keys: list[tuple] = []
        seen_keys: set[tuple] = set()
        for item in data:
            filtered_item = {k: v for k, v in item.items() if k in MARKET_DATA_COLUMNS}
            key = bar_key(filtered_item)
            if key is not None and key in seen_keys:
                logger.debug("Skipping duplicate bar in batch: %s", key)
                continue
            if key is not None:
                seen_keys.add(key)
                keys.append(key)
            logger.debug("Ready to insert record: %s", filtered_item)
            records.append((key, MarketDataModel(**filtered_item)))
        return records, keys

    def _write_bar(self, session, key, model) -> None:
        """Insert one bar, skipping it when the unique index already holds it."""
        try:
            session.add(model)
            session.commit()
        except IntegrityError:
            logger.debug("Bar already stored, skipped: %s", key)
            self._rollback(session)
        except DB_FAILURES as e:
            logger.error("Error writing market data to database: %s", e)
            self._rollback(session)

    def add_market_data(self, data: list[dict]) -> None:
        """Adds new market data records to the database in a single batched transaction.

        Duplicate bars are skipped rather than written twice: ``(symbol,
        timestamp)`` has a unique index, and a stream reconnect or a re-run
        hands the same minute bar back. Records are deduplicated within the
        batch, checked against what is already stored with one query, and a
        batch that still loses that check to a concurrent writer is retried one
        record at a time.
        """
        if not data:
            return

        session = self.connection.get_session()
        if not session:
            logger.warning("No session available")
            return

        # A write invalidates the cached interval anchors.
        self._interval_cache.clear()

        records: list[tuple] = []
        try:
            records, keys = self._prepare_bars(data)
            stored = self._stored_bar_keys(session, keys)
            fresh = [model for key, model in records if key is None or key not in stored]

            if not fresh:
                logger.info("All %d bars already stored; nothing written.", len(records))
                return

            session.add_all(fresh)
            session.commit()
            logger.info(
                "Database commit successful: %d new of %d bars.", len(fresh), len(records)
            )
        except IntegrityError as e:
            # Another writer stored one of these bars between the check and the
            # commit. Roll the batch back and keep the records that still fit.
            logger.warning("Duplicate bars in batch, retrying one at a time: %s", e)
            self._rollback(session)
            for key, model in records:
                self._write_bar(session, key, model)
        except DB_FAILURES as e:
            logger.error("Error writing market data to database: %s", e)
            self._rollback(session)
        finally:
            self.connection.close(session)

    def _stored_bar_keys(self, session, keys: list[tuple]) -> set:
        """Which of ``keys`` are already in ``market_data``.

        One query per batch: the symbols and timestamps are filtered in bulk and
        the exact pairs are matched in memory, so a batch of N bars costs one
        round trip instead of N.
        """
        if not keys:
            return set()
        try:
            symbols = sorted({symbol for symbol, _ in keys})
            timestamps = sorted({timestamp for _, timestamp in keys})
            rows = session.query(MarketDataModel.symbol, MarketDataModel.timestamp).filter(
                MarketDataModel.symbol.in_(symbols),
                MarketDataModel.timestamp.in_(timestamps),
            ).all()
            stored = set()
            for symbol, timestamp in rows:
                try:
                    stored.add((str(symbol).upper(), to_utc(timestamp)))
                except (TypeError, ValueError):
                    continue
            return stored
        except DB_FAILURES as e:
            logger.error("Error checking stored bars: %s", e)
            return set()

    def get_historical_market_data(
        self,
        symbol: str,
        start_time: datetime | None = None,
        limit: int = 2000
    ) -> list[dict]:
        """Retrieves historical market data / bars for a symbol since start_time."""
        session = self.connection.get_session()
        if not session:
            return []
        try:
            query = session.query(MarketDataModel).filter(
                MarketDataModel.symbol == symbol.upper()
            )
            if start_time is not None:
                query = query.filter(MarketDataModel.timestamp >= to_utc(start_time))
            if limit:
                # Retrieve the latest matching records ordered ascending efficiently
                records = (
                    query.order_by(MarketDataModel.timestamp.desc())
                    .limit(limit)
                    .all()
                )
                records.reverse()
            else:
                records = query.order_by(MarketDataModel.timestamp.asc()).all()

            return [
                {
                    "symbol": r.symbol,
                    "timestamp": epoch_seconds(r.timestamp) if isinstance(r.timestamp, datetime) else r.timestamp,
                    "price": float(r.close if r.close is not None else r.open or 0.0),
                    "open": float(r.open or 0.0),
                    "high": float(r.high or 0.0),
                    "low": float(r.low or 0.0),
                    "close": float(r.close or 0.0),
                    "volume": float(r.volume or 0.0),
                    "time_str": hhmmss(r.timestamp) if isinstance(r.timestamp, datetime) else str(r.timestamp)
                }
                for r in records
            ]
        except DB_FAILURES as e:
            logger.error("Error retrieving historical market data for %s: %s", symbol, e)
            return []
        finally:
            self.connection.close(session)

    def get_price_at_timestamp(self, symbol: str, target_timestamp: datetime) -> float | None:
        """Retrieves the price at a specific timestamp for interval calculation."""
        session = self.connection.get_session()
        if not session:
            return None
        try:
            query = session.query(MarketDataModel).filter(
                MarketDataModel.symbol == symbol.upper(),
                MarketDataModel.timestamp == to_utc(target_timestamp)
            ).first()
            if query:
                return float(query.close if query.close is not None else query.open or 0.0)
            return None
        except DB_FAILURES as e:
            logger.error(f"Error retrieving price at timestamp for {symbol}: {e}")
            return None
        finally:
            self.connection.close(session)

    def get_latest_price(self, symbol: str) -> float | None:
        """Retrieves the latest known price for a symbol."""
        session = self.connection.get_session()
        if not session:
            return None
        try:
            latest = session.query(MarketDataModel).filter(
                MarketDataModel.symbol == symbol.upper()
            ).order_by(MarketDataModel.timestamp.desc()).first()
            if latest:
                return float(latest.close if latest.close is not None else latest.open or 0.0)
            return None
        except DB_FAILURES as e:
            logger.error(f"Error retrieving latest price for {symbol}: {e}")
            return None
        finally:
            self.connection.close(session)

    def get_latest_market_record(self, symbol: str) -> dict[str, Any] | None:
        """Retrieves the latest market data record with full details."""
        session = self.connection.get_session()
        if not session:
            return None
        try:
            latest = session.query(MarketDataModel).filter(
                MarketDataModel.symbol == symbol.upper()
            ).order_by(MarketDataModel.timestamp.desc()).first()
            if latest:
                ts = latest.timestamp
                ts_val = epoch_seconds(ts) if isinstance(ts, datetime) else ts
                time_str = hhmmss(ts) if isinstance(ts, datetime) else str(ts)
                price = float(latest.close if latest.close is not None else latest.open or 0.0)
                return {
                    "symbol": latest.symbol,
                    "price": price,
                    "open": float(latest.open if latest.open is not None else price),
                    "high": float(latest.high if latest.high is not None else price),
                    "low": float(latest.low if latest.low is not None else price),
                    "close": float(latest.close if latest.close is not None else price),
                    "volume": float(latest.volume or 0.0),
                    "timestamp": ts_val,
                    "time_str": time_str
                }
            return None
        except DB_FAILURES as e:
            logger.error(f"Error retrieving latest market record for {symbol}: {e}")
            return None
        finally:
            self.connection.close(session)

    def get_price_at_interval_start(self, symbol: str, duration: str) -> float | None:
        """Retrieves the price at the start of a time interval for percentage calculation.

        Thin wrapper over :meth:`get_prices_at_interval_starts`, which is the one
        that shares its anchor read across durations.
        """
        return self.get_prices_at_interval_starts(symbol, [duration]).get(duration)

    def _read_cached_intervals(
        self, symbol_u: str, wanted: list[str], minute: str
    ) -> tuple[dict[str, float | None], list[str]]:
        """Split ``wanted`` durations into already-cached values and missing ones."""
        if minute != self._interval_cache_minute:
            self._interval_cache.clear()
            self._interval_cache_minute = minute

        prices: dict[str, float | None] = {}
        missing: list[str] = []
        for duration in wanted:
            key = (symbol_u, duration, minute)
            if key in self._interval_cache:
                prices[duration] = self._interval_cache[key]
            else:
                missing.append(duration)
        return prices, missing

    def _cache_missing_as_none(
        self, symbol_u: str, missing: list[str], minute: str, prices: dict[str, float | None]
    ) -> dict[str, float | None]:
        for duration in missing:
            prices[duration] = None
            self._interval_cache[(symbol_u, duration, minute)] = None
        return prices

    def get_prices_at_interval_starts(
        self,
        symbol: str,
        durations: list[str] | None = None,
    ) -> dict[str, float | None]:
        """Interval-start prices for several durations for one symbol.

        The dashboard asks for every duration (1h, 24h, 5d, 30d, 1y, ytd) per
        symbol per refresh, and the single-duration version paid two to four
        queries each time. Here the latest record is read once and every
        duration is measured against that same anchor, so a symbol costs one
        anchor query plus one per duration. Results are cached for the minute
        they were read in and the cache is cleared on every write, so a
        dashboard refreshing several times a minute pays the queries once.

        If markets were closed (a weekend, after hours), the interval is
        anchored to the latest available open market session so deltas remain
        meaningful.

        Args:
            symbol: The stock/crypto symbol
            durations: Time intervals (1h, 24h, 5d, 30d, 1y, ytd); defaults to all of them
        Returns:
            ``{duration: price}``, with ``None`` for a duration that has no data
        """
        wanted = list(durations) if durations else list(INTERVAL_DURATIONS)
        symbol_u = symbol.upper()
        minute = minute_key(now_utc())
        prices, missing = self._read_cached_intervals(symbol_u, wanted, minute)
        if not missing:
            return prices

        session = self.connection.get_session()
        if not session:
            return dict.fromkeys(wanted)
        try:
            # One anchor read shared by every duration.
            latest = session.query(MarketDataModel).filter(
                MarketDataModel.symbol == symbol_u
            ).order_by(MarketDataModel.timestamp.desc()).first()
            if not latest:
                return self._cache_missing_as_none(symbol_u, missing, minute, prices)
            latest_time = to_utc(latest.timestamp) if isinstance(latest.timestamp, datetime) else now_utc()

            for duration in missing:
                target_time, ref_end_time = interval_window(duration, now_utc(), latest_time)
                prices[duration] = self._interval_price(session, symbol_u, target_time, ref_end_time)
                self._interval_cache[(symbol_u, duration, minute)] = prices[duration]
            return prices
        except DB_FAILURES as e:
            logger.error(f"Error retrieving interval start prices for {symbol}: {e}")
            return self._cache_missing_as_none(symbol_u, missing, minute, prices)
        finally:
            self.connection.close(session)

    def _interval_price(self, session, symbol: str, target_time: datetime, ref_end_time: datetime) -> float | None:
        """The price at or just after ``target_time``, then just before it, then the oldest."""
        query = session.query(MarketDataModel).filter(
            MarketDataModel.symbol == symbol,
            MarketDataModel.timestamp >= target_time,
            MarketDataModel.timestamp <= ref_end_time
        ).order_by(MarketDataModel.timestamp.asc()).first()
        if query:
            return float(query.open if query.open is not None else query.close or 0.0)

        prev = session.query(MarketDataModel).filter(
            MarketDataModel.symbol == symbol,
            MarketDataModel.timestamp < target_time
        ).order_by(MarketDataModel.timestamp.desc()).first()
        if prev:
            return float(prev.close if prev.close is not None else prev.open or 0.0)

        oldest = session.query(MarketDataModel).filter(
            MarketDataModel.symbol == symbol
        ).order_by(MarketDataModel.timestamp.asc()).first()
        if oldest:
            return float(oldest.open if oldest.open is not None else oldest.close or 0.0)
        return None


    def get_model_weights(self, symbol: str):
        """Retrieves model weights for a specific symbol."""
        session = self.connection.get_session()
        if not session:
            return None
        try:
            query = session.query(ModelWeightsModel).filter_by(symbol=symbol).first()
            if query:
                try:
                    return json.loads(query.weights_json)
                except (json.JSONDecodeError, TypeError):
                    return None
            return None
        except DB_FAILURES as e:
            logger.error("Error retrieving model weights for %s: %s", symbol, e)
            return None
        finally:
            self.connection.close(session)

    def _write_weights(
        self,
        session,
        symbol: str,
        payload: str,
        trained_at: datetime
    ) -> None:
        """Update the record this key already holds, or claim an empty slot."""
        existing = session.query(ModelWeightsModel).filter_by(symbol=symbol).first()
        if existing:
            existing.weights_json = payload
            existing.last_trained = trained_at
        else:
            session.add(
                ModelWeightsModel(
                    symbol=symbol,
                    last_trained=trained_at,
                    weights_json=payload,
                )
            )

    def save_model_weights(self, symbol: str, weights: dict, last_trained: datetime | None = None) -> bool:
        """Persist the weights a training pass produced, so the next process can load them.

        ``symbol`` is the key :meth:`get_model_weights` reads back with. A value
        function is fit over a whole state vector, so when the vector covers
        several symbols the key is those symbols joined with a comma: the layout
        the weights belong to has to be recorded, because a vector of the wrong
        length is a silent nonsense decision rather than an error.

        Returns:
            True when the record was written, False when there is no database to
            write it to.
        """
        session = self.connection.get_session()
        if not session:
            return False
        try:
            payload = json.dumps(weights)
            trained_at = last_trained if last_trained is not None else now_utc()
            try:
                self._write_weights(session, symbol, payload, trained_at)
                session.commit()
            except IntegrityError:
                # Two processes trained the same key at once and both read an
                # empty slot, so both tried to claim it; the table's UNIQUE
                # constraint on `symbol` is what makes one of them lose. Roll
                # that attempt back and store against the record that won, so
                # the last training pass is what the next process loads.
                self._rollback(session)
                self._write_weights(session, symbol, payload, trained_at)
                session.commit()
            return True
        except DB_FAILURES as e:
            logger.error("Error saving model weights for %s: %s", symbol, e)
            self._rollback(session)
            return False
        finally:
            self.connection.close(session)

