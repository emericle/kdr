import unittest
from sqlalchemy import text
from src.database import DatabaseManager
import datetime
import json

class TestDatabase(unittest.TestCase):
    def setUp(self):
        # Use the QA database for live testing
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker
        from src.database import DEFAULT_QA_DATABASE_URL
    
        self.db_url = DEFAULT_QA_DATABASE_URL
        self.engine = create_engine(self.db_url)
        self.Session = sessionmaker(bind=self.engine)
    
        # Inject the custom engine into the DatabaseConnection class for testing
        import src.database as db_mod
        db_mod.DatabaseConnection.engine = self.engine
        db_mod.DatabaseConnection.session_factory = self.Session
    
        # Create tables
        self.db_manager = DatabaseManager(db_url=self.db_url)
        self.db_manager.connection.init_db()

        # Clear data before each test
        with self.engine.connect() as conn:
            try:
                conn.execution_options(isolation_level="AUTOCOMMIT")
                for table in ["market_data", "sentiment_data", "model_weights"]:
                    conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
            except Exception as e:
                # Tables might not exist yet during first run
                pass


    def test_add_market_data_success(self):
        # Test adding records to the database
        test_data = [{
            'symbol': 'AAPL',
            'timestamp': datetime.datetime.now(),
            'open': 150.0,
            'high': 160.0,
            'low': 140.0,
            'close': 155.0,
            'volume': 1000.0
        }]
        
        self.db_manager.add_market_data(test_data)
        
        # Verify data was actually saved to the database
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).filter_by(symbol='AAPL').all()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].close, 155.0)
        session.close()

    def test_get_model_weights_success(self):
        # Test retrieval of model weights
        symbol = 'TSLA'
        weights = {"w1": 0.8, "w2": 0.2}
        
        # Manually inject data into the DB
        session = self.db_manager.connection.get_session()
        from src.database import ModelWeightsModel
        model_record = ModelWeightsModel(
            symbol=symbol, 
            last_trained=datetime.datetime.now(), 
            weights_json=json.dumps(weights)
        )
        session.add(model_record)
        session.commit()
        session.close()

        # Test retrieval
        retrieved = self.db_manager.get_model_weights(symbol)
        self.assertEqual(retrieved, weights)
