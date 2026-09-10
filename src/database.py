"""Database management module for market data and model weights."""
import os
import json
import logging
from typing import List, Optional

from sqlalchemy import Column, Integer, Float, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base
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
            self.engine = create_engine(self.db_url, poolclass=QueuePool)
        
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

# Base Model for SQLAlchemy 2.0+ style or standard declaration
Base = declarative_base()

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

class DatabaseManager:
    """High-level manager for database operations."""
    def __init__(self, db_url: Optional[str] = None):
        self.connection = DatabaseConnection(db_url)

    def add_market_data(self, data: List[dict]):
        """Adds new market data records to the database."""
        session = self.connection.get_session()
        if not session:
            logger.warning("No session available")
            return

        try:
            for item in data:
                # Ensure only keys that belong to the model are included
                cols = MarketDataModel.__table__.columns.keys()
                filtered_item = {k: v for k, v in item.items() if k in cols}
                logger.debug("Ready to insert record: %s", filtered_item)
                new_record = MarketDataModel(**filtered_item)
                session.add(new_record)
            session.commit()
            logger.info("Database commit successful.")
        except Exception as e:
            logger.error("CRITICAL ERROR in DB write: %s", e)
            import traceback
            logger.debug(traceback.format_exc())
            session.rollback()
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

