# KDR Architecture & System Design

## Overview

KDR is an automated trading system designed around a **Bellman Decision model** to manage equity portfolios. The system runs on Python 3.14+, persisting market data in PostgreSQL, streaming live market data from Alpaca, incorporating sentiment analysis from Adanos, and rendering a real-time monitoring dashboard via FastAPI and WebSockets.

---

## Configuration

All credentials and endpoints come from `.env` in the repository root; nothing in `src/` carries a target of its own.

| Variable | Required | Notes |
| :--- | :--- | :--- |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Yes | Alpaca data and trading credentials (`ALPACA_BASE_URL` optional, defaults to paper trading) |
| `ADANOS_API_KEY` | Yes | Adanos sentiment API key |
| `DATABASE_URL` | Yes | The database the system writes to |
| `QA_DATABASE_URL` | No | Alternate database used when `SKIP_DB_TEST` is set or `USE_QA_DB` is true, so test traffic cannot reach a live book |

`get_database_url` resolves in one order — explicit override, then the QA database under the test flags, then `DATABASE_URL` — and returns an empty string with a logged error when none of them resolve. Connecting with an empty URL raises; the system never falls back to a database the operator did not name. `config_gatekeeper.validate_configs()` refuses to start when a required variable is missing.

---


## Core Components

### 1. Domain Models (`src/domain.py`)
Provides strictly validated Pydantic data schemas used across all components:
- `MarketState`: Price, volume, volatility, and sentiment metrics per symbol.
- `PortfolioState`: Cash balance, current holdings, and portfolio value.
- `FullState`: Composite state combining market and portfolio metrics with vectorization support for RL models.
- `Action`: Discrete decision states (`BUY`, `SELL`, `HOLD`).
- `TradeMovement`: Action accompanied by target symbol and execution sizing details.

### 2. Decision Engine & Reinforcement Learning (`src/model.py`)
- `BellmanEngine`: Computes Q-value targets using the Bellman optimality equation:
  $$Q(s, a) = R(s, a) + \gamma \max_{a'} Q(s', a')$$
- `RLTrainer`: Manages Gymnasium-compatible reinforcement learning environment training loops and model weight persistence.
- `ValueFunction`: Protocol defining state value estimation.

### 3. Data Pipeline & Adapters
- **Alpaca Streaming (`src/scraper.py`)**:
  - `AlpacaStreamProcessor`: Subscribes to live Alpaca WebSocket trade and bar feeds, with automated background flushing.
  - `DataStreamBuffer`: Thread-safe, $O(1)$ running OHLCV state aggregation and fast minute key generation, avoiding per-tick string formatting and $O(5N)$ traversals.
  - `DBWriterWorker`: Background worker draining tick and bar queues in batches for single-transaction persistence.
- **State Mapper (`src/state_mapper.py`)**:
  - Single-pass transformations mapping incoming raw ticks and bars into structured `MarketState` and `FullState` objects.
- **Sentiment & Data Adapters (`src/sentiment_adapter.py`, `src/data_adapters.py`)**:
  - `AdanosSentimentAdapter`: Queries Adanos API for sentiment scores and correlates sentiment with price action.
- **Persistence (`src/database.py`)**:
  - `DatabaseManager`: SQLAlchemy DeclarativeBase models with connection pooling and batched inserts (`session.add_all`) for high-throughput persistence.
  - Timestamps are stored as UTC-aware values (`DateTime(timezone=True)`), so PostgreSQL keeps the offset and range queries stay correct across DST and timezones. On read, `DatabaseManager` normalizes whatever the row holds back to a UTC-aware `datetime` before comparing it with the caller's boundary.
- **Time (`src/times.py`)**:
  - Single source of truth for every timestamp in the system. `now_utc`, `to_utc`, `parse_iso`, `epoch_seconds`, `from_epoch`, `epoch_minute`, `minute_key` and `hhmmss` are the only time primitives the other modules call; nothing in `src/` calls `datetime.now()` or `datetime.utcnow()`. The remaining `time.time()` calls measure elapsed durations (log rotation, stream timeouts, DB reconnect backoff), where the clock's offset is irrelevant.
  - `to_utc` buckets numeric epochs by magnitude (nanoseconds above `1e15`, milliseconds above `1e12`, seconds below), so Alpaca's millisecond and nanosecond feeds keep their precision instead of being truncated. A naive `datetime` is read as UTC; a value that is not a timestamp at all raises `TypeError`, which adapters turn into a `ValueError` naming the offending input.

### 4. Trading Execution & Risk Management (`src/trading_execution.py`)
- `PositionSizing`: Computes optimal trade sizes using the Kelly Criterion, bounded by a strict 20% portfolio capital allocation cap.
- `RiskManager`: Enforces cash availability, max exposure limits, and holding constraints.
- `OrderValidator`: Validates symbol names, order sides, and quantities before dispatch.
- `OrderTracker`: Incremental $O(1)$ position tracking that records order history and net position state.
- `AlpacaClient`: REST interface for order placement, account queries, and position retrieval with connection pooling (`requests.Session`).
- `TradingExecutor`: Coordinates Bellman model decisions through `RiskManager` and submits validated orders to `AlpacaClient`.

### 5. Web Server & Live Dashboard (`src/web_server.py`, `src/static/dashboard.html`)
- **FastAPI Backend**: Serves REST endpoints (`/api/market-indexes`, `/api/symbols`, `/api/status`) and real-time WebSocket feeds (`/ws`).
- **Persistent Index Cache**: Indexes (VIX, DJIA, SP500, NASDAQ, RUSSELL2000) persist to disk (`data/index_cache.json`) to prevent empty or $0.00 displays when markets are closed.
- **Market-Session Aware Deltas**: Lookback calculations anchor to the latest open market session rather than wall-clock time, handling weekends and market closures cleanly.
- **Frontend Dashboard**: Vue 3 + Chart.js interface with duration filtering (1h, 24h, 5d, 30d, 1y, YTD), dynamic color coding, and responsive charts.

### 6. Observability & Reporting (`src/reporting.py`)
- `CSVReporter`: Exports trade executions, decisions, and portfolio equity logs.
- `ChartGenerator`: Produces HTML/Plotly visualizations of portfolio performance against market benchmarks.
- `PerformanceAnalyzer`: Computes win rates, cumulative returns, profit factors, and average win/loss metrics.
- `MultiSymbolReporter` & `ReportScheduler`: Coordinates scheduled reporting runs across multiple tracked symbols.

---

## Data Flow

```text
[Alpaca WebSocket] ───► [DataStreamBuffer] ───► [DBWriterWorker] ───► [PostgreSQL]
                              │
                              ▼
                      [StateMapper] + [Adanos Sentiment]
                              │
                              ▼
                       [BellmanEngine] (Decision: Buy/Sell/Hold)
                              │
                              ▼
                   [RiskManager / PositionSizing (Kelly)]
                              │
                              ▼
                     [AlpacaClient] (Order Submission)
                              │
                              ▼
                   [Reporting & Dashboard WebSockets]
```
