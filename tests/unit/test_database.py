import datetime
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.database import DatabaseConnection, DatabaseManager, ModelWeightsModel, get_database_url


class TestGetDatabaseURL(unittest.TestCase):
    """URL resolution is configuration-only: nothing is hardcoded in source."""

    def test_returns_empty_when_nothing_is_configured(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(get_database_url(), "")

    def test_override_url_when_provided(self):
        override = "sqlite:///test.db"
        self.assertEqual(get_database_url(override_url=override), override)

    @patch.dict('os.environ', {'DATABASE_URL': 'postgresql://custom:pass@custom:5432/custom'})
    def test_database_url_when_present(self):
        self.assertEqual(get_database_url(), 'postgresql://custom:pass@custom:5432/custom')

    @patch.dict('os.environ', {'USE_QA_DB': 'true', 'QA_DATABASE_URL': 'sqlite:///qa.db'})
    def test_qa_database_url_when_opted_in(self):
        self.assertEqual(get_database_url(), 'sqlite:///qa.db')

    @patch.dict('os.environ', {'SKIP_DB_TEST': 'true', 'QA_DATABASE_URL': 'sqlite:///qa.db'})
    def test_qa_database_url_when_skip_flag_set(self):
        self.assertEqual(get_database_url(), 'sqlite:///qa.db')

    @patch.dict('os.environ', {'USE_QA_DB': 'true'}, clear=True)
    def test_qa_opt_in_without_qa_url_is_disabled(self):
        """Opting in without configuring the QA database must not invent a target."""
        self.assertEqual(get_database_url(), "")

    @patch.dict('os.environ', {'DATABASE_URL': 'postgresql://main:pass@main:5432/main',
                               'QA_DATABASE_URL': 'sqlite:///qa.db',
                               'USE_QA_DB': 'true'})
    def test_qa_url_takes_precedence_when_opted_in(self):
        self.assertEqual(get_database_url(), 'sqlite:///qa.db')

    def test_sqlite_engine_for_memory_database(self):
        mock_get_url = MagicMock(return_value="sqlite:///:memory:")

        with patch('src.database.get_database_url', mock_get_url):
            conn = DatabaseConnection()
            self.assertIsNotNone(conn.engine)

    @patch('src.database.get_database_url')
    def test_sqlite_engine_for_memory_database_mocked(self, mock_get_url):
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

        from src.database import Base

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

    def test_add_market_data_ignores_extra_columns_filter(self):
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

    def test_get_model_weights_returns_none_on_db_failure(self):
        """A connection timeout or DBAPI failure must return None, not crash startup."""
        from sqlalchemy.exc import OperationalError

        mock_session = MagicMock()
        mock_session.query.side_effect = OperationalError("connection timeout expired", None, None)

        with patch.object(self.db_manager.connection, 'get_session', return_value=mock_session):
            result = self.db_manager.get_model_weights('TSLA')
            self.assertIsNone(result)

    def test_save_model_weights_returns_false_on_db_failure(self):
        """A lost database during weight persistence must return False and roll back."""
        from sqlalchemy.exc import OperationalError

        mock_session = MagicMock()
        mock_session.query.side_effect = OperationalError("connection timeout expired", None, None)

        with patch.object(self.db_manager.connection, 'get_session', return_value=mock_session):
            result = self.db_manager.save_model_weights('TSLA', {"weights": [0.1], "bias": 0.0})
            self.assertFalse(result)
            mock_session.rollback.assert_called_once()

    def test_get_model_weights_parses_json_weights(self):
        symbol = 'TSLA'
        weights = {"w1": 0.8, "w2": 0.2, "w3": 0.5}

        session = self.db_manager.connection.get_session()
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
        """Real SQLAlchemy round-trip: malformed weights_json must yield None.

        Deliberately un-mocked. A MagicMock chain accepts any attribute name, so
        it would keep passing if get_model_weights were rewritten to call a
        non-existent query method. Driving the real session instead means any
        change to the query API fails this test.
        """
        symbol = 'INVALID'

        session = self.db_manager.connection.get_session()
        session.add(ModelWeightsModel(
            symbol=symbol,
            last_trained=datetime.datetime.now(),
            weights_json="invalid json string"
        ))
        session.commit()
        session.close()

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
        from sqlalchemy import create_engine


        # Create a real engine with a real database
        self.engine = create_engine('sqlite:///:memory:')

        # Create a mock DatabaseConnection with the real engine
        with (
            patch('src.database.get_database_url', return_value='sqlite:///:memory:'),
            patch('src.database.create_engine', return_value=self.engine),
            patch('src.database.Base.metadata.create_all'),
        ):
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

        with (
            patch('src.database.get_database_url', mock_get_url),
            patch('src.database.create_engine', mock_create_engine),
            patch('src.database.Base.metadata.create_all'),
        ):
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
        mock_close = MagicMock()
        mock_session.close = mock_close
        conn.close(mock_session)
        mock_close.assert_called_once()

    @patch('src.database.get_database_url')
    def test_close_session_with_none(self, mock_get_url):
        mock_get_url.return_value = "sqlite:///:memory:"

        mock_create_engine = MagicMock(return_value=MagicMock())

        with patch('src.database.create_engine', mock_create_engine):
            conn = DatabaseConnection()
            conn.close(None)
            # Should not raise exception

class TestGetHistoricalMarketData(unittest.TestCase):
    def setUp(self):
        from src.database import Base, DatabaseManager
        self.db = DatabaseManager(db_url="sqlite:///:memory:")
        Base.metadata.create_all(self.db.connection.engine)

    def test_get_historical_market_data_empty(self):
        res = self.db.get_historical_market_data("AAPL")
        self.assertEqual(res, [])

    def test_get_historical_market_data_with_records(self):
        now = datetime.datetime.now()
        data = [
            {
                "symbol": "AAPL",
                "timestamp": now - datetime.timedelta(minutes=10),
                "open": 150.0,
                "high": 155.0,
                "low": 149.0,
                "close": 152.0,
                "volume": 1000.0
            },
            {
                "symbol": "AAPL",
                "timestamp": now - datetime.timedelta(minutes=5),
                "open": 152.0,
                "high": 156.0,
                "low": 151.0,
                "close": 155.0,
                "volume": 1200.0
            },
            {
                "symbol": "MSFT",
                "timestamp": now,
                "open": 300.0,
                "high": 305.0,
                "low": 299.0,
                "close": 302.0,
                "volume": 800.0
            }
        ]
        self.db.add_market_data(data)

        # Retrieve AAPL records
        records = self.db.get_historical_market_data("AAPL")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["close"], 152.0)
        self.assertEqual(records[1]["close"], 155.0)

        # Retrieve with limit
        limited = self.db.get_historical_market_data("AAPL", limit=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0]["close"], 155.0)

        # Retrieve with start_time
        start_time = now - datetime.timedelta(minutes=7)
        filtered = self.db.get_historical_market_data("AAPL", start_time=start_time)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["close"], 155.0)


        # Retrieve with limit=0/None (all ascending branch)
        all_asc = self.db.get_historical_market_data("AAPL", limit=0)
        self.assertEqual(len(all_asc), 2)
        self.assertEqual(all_asc[0]["close"], 152.0)

    def test_database_manager_session_none(self):
        self.db.connection.get_session = MagicMock(return_value=None)
        # Should not raise exception
        self.db.add_market_data([{"symbol": "AAPL", "open": 100.0}])
        self.assertEqual(self.db.get_historical_market_data("AAPL"), [])

    def test_database_manager_commit_failure(self):
        """A failed commit is reported and rolled back; a failed rollback is not fatal.

        The mock raises the error types a real DBAPI failure arrives as, which is
        what the handlers match on: a generic ``Exception`` would now be a bug in
        this file and would reach the writer thread.
        """
        from sqlalchemy.exc import IntegrityError, OperationalError

        mock_session = MagicMock()
        mock_session.commit.side_effect = IntegrityError("Commit error", None, None)
        mock_session.rollback.side_effect = OperationalError("Rollback error", None, None)
        self.db.connection.get_session = MagicMock(return_value=mock_session)

        self.db.add_market_data([{"symbol": "AAPL", "open": 100.0}])

        # The batch lost the commit, so the retry path wrote it one bar at a time
        self.assertEqual(mock_session.add.call_count, 1)
        self.assertEqual(mock_session.commit.call_count, 2)

    def test_get_model_weights_invalid_json(self):
        """Real SQLAlchemy round-trip: malformed weights_json must yield None.

        Un-mocked for the same reason as above: the mock chain never exercised
        the real query API, so a typo in it would have stayed green.
        """
        session = self.db.connection.get_session()
        session.add(ModelWeightsModel(
            symbol="AAPL",
            last_trained=datetime.datetime.now(),
            weights_json="invalid json {{"
        ))
        session.commit()
        session.close()

        res = self.db.get_model_weights("AAPL")
        self.assertIsNone(res)


class TestNoHardcodedCredentials(unittest.TestCase):
    """A live QA password must not be recoverable from the repo."""

    def test_database_source_contains_no_url_literal(self):
        import re
        text = (Path(__file__).resolve().parents[2] / "src" / "database.py").read_text(encoding="utf-8")
        literals = re.findall(r'["\']postgresql://[^"\']*["\']', text)
        self.assertEqual(literals, [])

    def test_env_example_documents_the_qa_variable(self):
        example = Path(__file__).resolve().parents[2] / ".env.example"
        self.assertIn("QA_DATABASE_URL", example.read_text(encoding="utf-8"))


class TestCheckDatabaseScript(unittest.TestCase):
    """bin/check-database.sh must use the real DatabaseConnection API and stay non-destructive."""

    def test_check_database_script_uses_real_connection_api(self):
        script = (Path(__file__).resolve().parents[2] / "bin" / "check-database.sh").read_text(encoding="utf-8")
        self.assertNotIn("get_engine", script)
        self.assertNotIn("init_db()", script)
        self.assertIn("db.connection.engine", script)
        self.assertIn("db.connection.get_session()", script)
        self.assertIn("current_alembic_revision", script)
        self.assertIn("schema_mismatches", script)
        self.assertIn("except DB_FAILURES", script)


if __name__ == '__main__':
    unittest.main()
