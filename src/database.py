"""Database management module for market data and model weights."""
import os
import json
import logging
from typing import List, Optional

from sqlalchemy import Column, Integer, Float, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool
from sqlalchemy import create_engine
from dotenv import load_dotenv

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

DEFAULT_QA_DATABASE_URL = "postgresql://slop:sloppyslop1!@localhost:5432/qa_instance"

def get_database_url(override_url: Optional[str] = None) -> str:
    if override_url:
        return override_url
    if os.getenv("SKIP_DB_TEST") or os.getenv("USE_QA_DB"):
        return os.getenv("QA_DATABASE_URL", DEFAULT_QA_DATABASE_URL)
    env_url = os.getenv("DATABASE_URL")
    if not env_url:
        logger.info("No DATABASE_URL found, using qa_instance database for operations.")
        return DEFAULT_QA_DATABASE_URL
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
                connect_args={"connect_timeout": 5}
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
    """Schema for market data records."""
    __tablename__ = 'market_data'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    timestamp = Column(DateTime, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)

class SentimentDataModel(Base):
    """Schema for sentiment data records."""
    __tablename__ = 'sentiment_data'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, index=True)
    timestamp = Column(DateTime)
    score = Column(Float)
    source = Column(String)

class ModelWeightsModel(Base):
    """Schema for model weights records."""
    __tablename__ = 'model_weights'
    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True)
    last_trained = Column(DateTime)
    weights_json = Column(Text)

MARKET_DATA_COLUMNS = {"symbol", "timestamp", "open", "high", "low", "close", "volume"}

class DatabaseManager:
    """High-level manager for database operations."""
    def __init__(self, db_url: Optional[str] = None):
        self.connection = DatabaseConnection(db_url)

    def add_market_data(self, data: List[dict]) -> None:
        """Adds new market data records to the database in a single batched transaction."""
        if not data:
            return

        session = self.connection.get_session()
        if not session:
            logger.warning("No session available")
            return

        try:
            records = []
            for item in data:
                filtered_item = {k: v for k, v in item.items() if k in MARKET_DATA_COLUMNS}
                logger.debug("Ready to insert record: %s", filtered_item)
                records.append(MarketDataModel(**filtered_item))

            session.add_all(records)
            session.commit()
            logger.info("Database commit successful.")
        except Exception as e:
            logger.error("CRITICAL ERROR in DB write: %s", e)
            import traceback
            logger.debug(traceback.format_exc())
            session.rollback()
        finally:
            self.connection.close(session)

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
                query = query.filter(MarketDataModel.timestamp >= start_time)
            query = query.order_by(MarketDataModel.timestamp.asc())
            if limit:
                # If limited, take latest matching records ordered ascending
                records = query.all()
                if len(records) > limit:
                    records = records[-limit:]
            else:
                records = query.all()

            return [
                {
                    "symbol": r.symbol,
                    "timestamp": r.timestamp.timestamp() if isinstance(r.timestamp, datetime) else r.timestamp,
                    "price": float(r.close if r.close is not None else r.open or 0.0),
                    "open": float(r.open or 0.0),
                    "high": float(r.high or 0.0),
                    "low": float(r.low or 0.0),
                    "close": float(r.close or 0.0),
                    "volume": float(r.volume or 0.0),
                    "time_str": r.timestamp.strftime("%H:%M:%S") if isinstance(r.timestamp, datetime) else str(r.timestamp)
                }
                for r in records
            ]
        except Exception as e:
            logger.error("Error retrieving historical market data for %s: %s", symbol, e)
            return []
        finally:
            self.connection.close(session)

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

