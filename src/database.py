"""Database management module for market data and model weights."""
import os
import json
import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Integer, Float, String, DateTime, Text, Index
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from sqlalchemy import create_engine
from dotenv import load_dotenv

from src.times import epoch_seconds, from_epoch, hhmmss, minute_key, now_utc, parse_iso, to_utc

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

def get_database_url(override_url: Optional[str] = None) -> str:
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

DATABASE_URL = get_database_url()

class DatabaseConnection:
    """Handles the underlying SQL connection and session factory."""
    def __init__(self, db_url: Optional[str] = None):
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

    def init_db(self):
        """Initializes the database schema."""
        if self.engine:
            try:
                logger.info("Initialising database tables...")
                Base.metadata.create_all(self.engine)
                logger.info("Database initialized successfully.")
            except Exception as e:
                logger.warning(f"Notice: Database initialization skipped: {e}")



    def close(self, session):
        """Closes an active session."""
        if session:
            session.close()

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


def bar_key(item: Dict[str, Any]) -> Optional[tuple]:
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
            return datetime(base_year, 1, 1, tzinfo=timezone.utc), now
        return now - INTERVAL_OFFSETS["24h"], now
    ref_end_time = latest_time if (now - latest_time) > offset else now
    return ref_end_time - offset, ref_end_time


class DatabaseManager:
    """High-level manager for database operations."""
    def __init__(self, db_url: Optional[str] = None):
        self.connection = DatabaseConnection(db_url)
        # Interval-start prices are read several times per dashboard refresh;
        # they are kept for the minute they were read in and dropped on write.
        self._interval_cache: Dict[tuple, Optional[float]] = {}
        self._interval_cache_minute: Optional[str] = None

    def add_market_data(self, data: List[dict]) -> None:
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

        try:
            records: List[tuple] = []
            seen: List[tuple] = []
            seen_keys = set()
            for item in data:
                filtered_item = {k: v for k, v in item.items() if k in MARKET_DATA_COLUMNS}
                key = bar_key(filtered_item)
                if key is not None:
                    if key in seen_keys:
                        logger.debug("Skipping duplicate bar in batch: %s", key)
                        continue
                    seen_keys.add(key)
                    seen.append(key)
                logger.debug("Ready to insert record: %s", filtered_item)
                records.append((key, MarketDataModel(**filtered_item)))

            stored = self._stored_bar_keys(session, seen)
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
            try:
                session.rollback()
            except Exception:
                pass
            for key, model in records:
                try:
                    session.add(model)
                    session.commit()
                except IntegrityError:
                    logger.debug("Bar already stored, skipped: %s", key)
                    try:
                        session.rollback()
                    except Exception:
                        pass
                except Exception as e:
                    logger.error("Error writing market data to database: %s", e)
                    try:
                        session.rollback()
                    except Exception:
                        pass
        except Exception as e:
            logger.error("Error writing market data to database: %s", e)
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            self.connection.close(session)

    def _stored_bar_keys(self, session, keys: List[tuple]) -> set:
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
        except Exception as e:
            logger.error("Error checking stored bars: %s", e)
            return set()

    def get_historical_market_data(
        self,
        symbol: str,
        start_time: Optional[datetime] = None,
        limit: int = 2000
    ) -> List[dict]:
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
        except Exception as e:
            logger.error("Error retrieving historical market data for %s: %s", symbol, e)
            return []
        finally:
            self.connection.close(session)

    def get_price_at_timestamp(self, symbol: str, target_timestamp: datetime) -> Optional[float]:
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
        except Exception as e:
            logger.error(f"Error retrieving price at timestamp for {symbol}: {e}")
            return None
        finally:
            self.connection.close(session)

    def get_latest_price(self, symbol: str) -> Optional[float]:
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
        except Exception as e:
            logger.error(f"Error retrieving latest price for {symbol}: {e}")
            return None
        finally:
            self.connection.close(session)

    def get_latest_market_record(self, symbol: str) -> Optional[Dict[str, Any]]:
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
        except Exception as e:
            logger.error(f"Error retrieving latest market record for {symbol}: {e}")
            return None
        finally:
            self.connection.close(session)

    def get_price_at_interval_start(self, symbol: str, duration: str) -> Optional[float]:
        """Retrieves the price at the start of a time interval for percentage calculation.

        Thin wrapper over :meth:`get_prices_at_interval_starts`, which is the one
        that shares its anchor read across durations.
        """
        return self.get_prices_at_interval_starts(symbol, [duration]).get(duration)

    def get_prices_at_interval_starts(
        self,
        symbol: str,
        durations: Optional[List[str]] = None,
    ) -> Dict[str, Optional[float]]:
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
        if minute != self._interval_cache_minute:
            self._interval_cache.clear()
            self._interval_cache_minute = minute

        prices: Dict[str, Optional[float]] = {}
        missing: List[str] = []
        for duration in wanted:
            key = (symbol_u, duration, minute)
            if key in self._interval_cache:
                prices[duration] = self._interval_cache[key]
            else:
                missing.append(duration)
        if not missing:
            return prices

        session = self.connection.get_session()
        if not session:
            return {duration: None for duration in wanted}
        try:
            # One anchor read shared by every duration.
            latest = session.query(MarketDataModel).filter(
                MarketDataModel.symbol == symbol_u
            ).order_by(MarketDataModel.timestamp.desc()).first()
            if not latest:
                for duration in missing:
                    prices[duration] = None
                return prices
            latest_time = to_utc(latest.timestamp) if isinstance(latest.timestamp, datetime) else now_utc()

            for duration in missing:
                target_time, ref_end_time = interval_window(duration, now_utc(), latest_time)
                prices[duration] = self._interval_price(session, symbol_u, target_time, ref_end_time)
                key = (symbol_u, duration, minute)
                self._interval_cache[key] = prices[duration]
            return prices
        except Exception as e:
            logger.error(f"Error retrieving interval start prices for {symbol}: {e}")
            return {duration: None for duration in wanted}
        finally:
            self.connection.close(session)

    def _interval_price(self, session, symbol: str, target_time: datetime, ref_end_time: datetime) -> Optional[float]:
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
        finally:
            self.connection.close(session)

