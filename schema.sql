-- schema.sql — reference copy of the schema defined by the SQLAlchemy models in
-- src/database.py. `DatabaseConnection.init_db()` creates these tables from
-- `Base.metadata`; this file is the same schema rendered as SQL so it can be
-- loaded with psql when inspecting or bootstrapping a database by hand. Keep it
-- in step with the models: tests/unit/test_schema_sql.py fails if they drift.
--
-- Float columns are DOUBLE PRECISION, the explicit spelling of the IEEE-754
-- double SQLAlchemy sends for `Column(Float)`; PostgreSQL's `FLOAT` alias means
-- the same thing, but MySQL's `FLOAT` is single precision, so the explicit name
-- is the only one that is unambiguous across backends.

CREATE TABLE market_data (
    id INTEGER PRIMARY KEY,
    symbol VARCHAR,
    timestamp TIMESTAMP WITH TIME ZONE,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION
);
CREATE INDEX idx_symbol ON market_data (symbol);
CREATE INDEX idx_timestamp ON market_data (timestamp);
-- One bar per (symbol, timestamp): a stream reconnect or a re-run hands the
-- same minute bar back, and `add_market_data` skips the duplicate rather than
-- writing it twice.
CREATE UNIQUE INDEX market_data_bar_unique ON market_data (symbol, timestamp);

CREATE TABLE sentiment_data (
    id INTEGER PRIMARY KEY,
    symbol VARCHAR,
    timestamp TIMESTAMP WITH TIME ZONE,
    score DOUBLE PRECISION,
    source VARCHAR
);
CREATE INDEX idx_symbol ON sentiment_data (symbol);

CREATE TABLE model_weights (
    id INTEGER PRIMARY KEY,
    symbol VARCHAR UNIQUE,
    last_trained TIMESTAMP WITH TIME ZONE,
    weights_json TEXT
);
