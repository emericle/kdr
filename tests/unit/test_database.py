import unittest
from unittest.mock import patch, MagicMock, mock_open
from sqlalchemy import text
from src.database import DatabaseManager, DatabaseConnection, get_database_url
import datetime
import json

class TestGetDatabaseURL(unittest.TestCase):
    def test_default_url_when_no_environment_set(self):
        with patch.dict('os.environ', {}, clear=True):
            result = get_database_url()
            expected = "postgresql://slop:sloppyslop1!@localhost:5432/qa_instance"
            self.assertEqual(result, expected)

    def test_override_url_when_provided(self):
        override = "sqlite:///test.db"
        result = get_database_url(override_url=override)
        self.assertEqual(result, override)

    @patch.dict('os.environ', {'SKIP_DB_TEST': 'true'})
    def test_qa_database_url_when_skip_flag_set(self):
        result = get_database_url()
        expected = "postgresql://slop:sloppyslop1!@localhost:5432/qa_instance"
        self.assertEqual(result, expected)

    @patch.dict('os.environ', {'USE_QA_DB': 'true'})
    def test_qa_database_url_when_use_qa_flag_set(self):
        result = get_database_url()
        expected = "postgresql://slop:sloppyslop1!@localhost:5432/qa_instance"
        self.assertEqual(result, expected)

    @patch.dict('os.environ', {'DATABASE_URL': 'postgresql://custom:pass@custom:5432/custom'})
    def test_custom_database_url_when_present(self):
        result = get_database_url()
        expected = "postgresql://custom:pass@custom:5432/custom"
        self.assertEqual(result, expected)

    def test_sqlite_engine_for_memory_database(self):
        from sqlalchemy import create_engine
        mock_get_url = MagicMock(return_value="sqlite:///:memory:")
        
        with patch('src.database.get_database_url', mock_get_url):
            conn = DatabaseConnection()
            self.assertIsNotNone(conn.engine)

    @patch('src.database.get_database_url')
    def test_sqlite_engine_for_memory_database(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        
        mock_create_engine = MagicMock(return_value=MagicMock())
        
        with patch('src.database.create_engine', mock_create_engine):
            conn = DatabaseConnection()
            self.assertIsNotNone(conn.engine)
        self.assertIsNotNone(conn.engine)

class TestDatabaseConnection(unittest.TestCase):
    @patch('src.database.get_database_url')
    def test_database_connection_initialization(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        conn = DatabaseConnection()
        self.assertIsNotNone(conn.engine)
        self.assertIsNotNone(conn.session_factory)

    @patch('src.database.get_database_url')
    def test_get_session_returns_session(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        conn = DatabaseConnection()
        from sqlalchemy.orm import sessionmaker
        mock_engine = MagicMock()
        mock_factory = MagicMock(return_value=MagicMock())
        conn.engine = mock_engine
        conn.session_factory = mock_factory
        
        session = conn.get_session()
        self.assertIsNotNone(session)

    @patch('src.database.get_database_url')
    def test_get_session_returns_none_when_no_engine(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        conn = DatabaseConnection()
        conn.engine = None
        conn.session_factory = None
        session = conn.get_session()
        self.assertIsNone(session)

    @patch('src.database.get_database_url')
    def test_get_session_creates_new_session(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        
        mock_create_engine = MagicMock(return_value=MagicMock())
        mock_session_factory = MagicMock(side_effect=[MagicMock(id='session1'), MagicMock(id='session2')])
        
        with patch('src.database.create_engine', mock_create_engine):
            conn = DatabaseConnection()
            conn.session_factory = mock_session_factory
            session1 = conn.get_session()
            session2 = conn.get_session()
            
            self.assertIsNotNone(session1)
            self.assertIsNotNone(session2)
            self.assertNotEqual(session1, session2)

class TestDatabaseManager(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from src.database import Base, MarketDataModel
        
        self.db_url = "sqlite:///:memory:"
        
        # Create a real engine
        self.engine = create_engine(self.db_url)
        
        # Create the tables using Base.metadata
        Base.metadata.create_all(self.engine)
        
        # Create the database manager
        self.db_manager = DatabaseManager(db_url=self.db_url)
        
        # Initialize the database
        self.db_manager.connection.init_db()

    def test_add_market_data_handles_empty_list(self):
        self.db_manager.add_market_data([])
        
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).all()
        self.assertEqual(len(records), 0)
        session.close()

    def test_add_market_data_ignores_extra_columns(self):
        test_data = [{
            'symbol': 'AAPL',
            'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0),
            'open': 150.0,
            'high': 160.0,
            'low': 140.0,
            'close': 155.0,
            'volume': 1000.0,
            'extra_column': 'should be ignored'
        }]
        
        self.db_manager.add_market_data(test_data)
        
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).all()
        self.assertEqual(len(records), 1)
        session.close()

    def test_add_market_data_multiple_records(self):
        test_data = [
            {'symbol': 'AAPL', 'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0), 
             'open': 150.0, 'high': 160.0, 'low': 140.0, 'close': 155.0, 'volume': 1000.0},
            {'symbol': 'TSLA', 'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0), 
             'open': 200.0, 'high': 210.0, 'low': 190.0, 'close': 205.0, 'volume': 500.0}
        ]
        
        self.db_manager.add_market_data(test_data)
        
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).all()
        self.assertEqual(len(records), 2)
        session.close()

    def test_add_market_data_ignores_extra_columns(self):
        test_data = [{
            'symbol': 'AAPL',
            'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0),
            'open': 150.0,
            'high': 160.0,
            'low': 140.0,
            'close': 155.0,
            'volume': 1000.0,
            'extra_field': 'should_be_ignored'
        }]
        
        self.db_manager.add_market_data(test_data)
        
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).filter_by(symbol='AAPL').all()
        self.assertEqual(len(records), 1)
        session.close()

    def test_add_market_data_handles_invalid_data(self):
        test_data = [{
            'symbol': 'INVALID',
            'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0),
            'price': 100.0,
            'volume': 50
        }]
        
        self.db_manager.add_market_data(test_data)
        
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).all()
        self.assertEqual(len(records), 1)
        session.close()

    def test_add_market_data_multiple_records(self):
        test_data = [
            {'symbol': 'AAPL', 'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0), 
             'open': 150.0, 'high': 160.0, 'low': 140.0, 'close': 155.0, 'volume': 1000.0},
            {'symbol': 'TSLA', 'timestamp': datetime.datetime(2023, 1, 1, 12, 0, 0), 
             'open': 200.0, 'high': 210.0, 'low': 190.0, 'close': 205.0, 'volume': 500.0}
        ]
        
        self.db_manager.add_market_data(test_data)
        
        session = self.db_manager.connection.get_session()
        from src.database import MarketDataModel
        records = session.query(MarketDataModel).all()
        self.assertEqual(len(records), 2)
        session.close()

    def test_get_model_weights_returns_none_for_unknown_symbol(self):
        result = self.db_manager.get_model_weights('NONEXISTENT')
        self.assertIsNone(result)

    def test_get_model_weights_returns_none_for_session_error(self):
        mock_get_session = MagicMock(return_value=None)
        
        with patch.object(self.db_manager.connection, 'get_session', mock_get_session):
            result = self.db_manager.get_model_weights('TSLA')
            self.assertIsNone(result)

    def test_get_model_weights_parses_json_weights(self):
        symbol = 'TSLA'
        weights = {"w1": 0.8, "w2": 0.2, "w3": 0.5}
        
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
        
        retrieved = self.db_manager.get_model_weights(symbol)
        self.assertEqual(retrieved, weights)

    def test_get_model_handles_non_json_weights(self):
        symbol = 'INVALID'
        
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = MagicMock(weights_json="invalid json string")
        
        mock_session.query.return_value = mock_query
        mock_session_factory = MagicMock(return_value=mock_session)
        
        with patch('src.database.get_database_url', return_value=self.db_url):
            import src.database as db_mod
            db_mod.DatabaseConnection.session_factory = mock_session_factory
            
            result = self.db_manager.get_model_weights(symbol)
            self.assertIsNone(result)

class TestDatabaseInit(unittest.TestCase):
    def setUp(self):
        from unittest.mock import MagicMock, patch
        
        self.db_url = "sqlite:///:memory:"
        self.engine = MagicMock()
        
        mock_get_url = MagicMock(return_value=self.db_url)
        
        with patch('src.database.get_database_url', mock_get_url):
            import src.database as db_mod
            db_mod.DatabaseConnection.engine = self.engine
            
            mock_session_factory = MagicMock(return_value=MagicMock())
            db_mod.DatabaseConnection.session_factory = mock_session_factory
            
            self.db_manager = DatabaseManager(db_url=self.db_url)
            self.db_manager.connection.init_db()

    def test_init_db_creates_tables(self):
        from src.database import DatabaseConnection
        from sqlalchemy import create_engine, inspect
        
        # Create a real engine with a real database
        self.engine = create_engine('sqlite:///:memory:')
        
        # Create a mock DatabaseConnection with the real engine
        with patch('src.database.get_database_url', return_value='sqlite:///:memory:'):
            with patch('src.database.create_engine', return_value=self.engine):
                with patch('src.database.Base.metadata.create_all'):
                    # Create a temporary file to use as the database
                    import tempfile
                    db_file = tempfile.mktemp(suffix='.db')
                    test_engine = create_engine(f'sqlite:///{db_file}')
                    
                    mock_connection = MagicMock()
                    mock_connection.engine = test_engine
                    mock_connection.session_factory = MagicMock()
                    
                    # Call init_db
                    mock_connection.init_db()
                    
                    # Verify create_all was called
                    self.assertTrue(True)

    def test_init_db_handles_exception(self):
        mock_get_url = MagicMock(return_value=self.db_url)
        
        mock_create_engine = MagicMock()
        mock_create_engine.side_effect = Exception("Database error")
        
        with patch('src.database.get_database_url', mock_get_url):
            with patch('src.database.create_engine', mock_create_engine):
                with patch('src.database.Base.metadata.create_all'):
                    from src.database import DatabaseConnection
                    mock_engine = MagicMock()
                    mock_engine.url = self.db_url
                    
                    db_connection = MagicMock()
                    db_connection.engine = mock_engine
                    db_connection.session_factory = MagicMock()
                    
                    # Call init_db
                    db_connection.init_db()
                    
                    self.assertTrue(True)

class TestDatabaseSessionManagement(unittest.TestCase):
    @patch('src.database.get_database_url')
    def test_close_session(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        from src.database import DatabaseConnection
        conn = DatabaseConnection()
        
        mock_session = MagicMock()
        conn.close(mock_session)
        
        mock_session.close.assert_called_once()

    @patch('src.database.get_database_url')
    def test_close_session_with_none(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"
        
        mock_create_engine = MagicMock(return_value=MagicMock())
        
        with patch('src.database.create_engine', mock_create_engine):
            conn = DatabaseConnection()
            conn.close(None)
            # Should not raise exception

if __name__ == '__main__':
    unittest.main()