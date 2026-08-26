-- Trade System Database Schema
-- Created for local hosting on PostgreSQL

\i 'market_data' TO market_data;
\i 'sentiment_data' TO sentiment_data;
\i 'model_weights' TO model_weights;

CREATE TABLE IF NOT EXISTS market_data (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    open FLOAT,
    high FLOAT,
    low FLOAT,
    close FLOAT,
    volume FLOAT
);
CREATE INDEX idx_market_data_symbol ON market_data(symbol);
CREATE INDEX idx_market_data_ts ON market_data(timestamp);

CREATE TABLE IF NOT EXISTS sentiment_data (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    score FLOAT,
    source VARCHAR(50)
);
CREATE INDEX idx_sentiment_symbol ON sentiment_data(symbol);

CREATE TABLE IF NOT EXISTS model_weights (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) UNIQUE NOT NULL,
    last_trained TIMESTAMP NOT NULL,
    weights_json TEXT NOT NULL
);
