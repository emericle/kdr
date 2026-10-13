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
| `KDR_STATE_VECTOR_SCHEMA` | No | `normalized` (default) or `legacy`; chooses the state vector layout so a run can be compared against the pre-normalization model |
| `BACKTEST_SYMBOLS` / `BACKTEST_CASH` / `BACKTEST_MINUTES` | No | Only read by `python -m src.backtest`: the symbols to replay, the paper portfolio's starting cash (default 100000) and the window of stored bars to replay (default 1440 minutes) |

`get_database_url` resolves in one order — explicit override, then the QA database under the test flags, then `DATABASE_URL` — and returns an empty string with a logged error when none of them resolve. Connecting with an empty URL raises; the system never falls back to a database the operator did not name. `config_gatekeeper.validate_configs()` refuses to start when a required variable is missing.

---


## Core Components

### 1. Domain Models (`src/domain.py`)
Provides strictly validated Pydantic data schemas used across all components:
- `MarketState`: Prices, previous prices, and sentiment metrics per symbol, plus the `sentiment_min`/`sentiment_max` bounds of the scale the scores were produced on.
- `PortfolioState`: Cash balance, current holdings, and derived portfolio value.
- `FullState`: Composite state with vectorization support for the RL model, and a `symbols` view of everything the state carries.
- `Action`: Discrete decision states (`BUY`, `SELL`, `HOLD`) with an optional target-weight payload.
- `TradeMovement`: The discrete movement enum the action space enumerates.
- `candidate_actions`: Enumerates BUY/SELL/HOLD per symbol, which is what the engine scores.

`to_vector` emits `3 * len(symbols) + 1` scaled features per state: log price change, sentiment rescaled onto roughly [-1, 1] using the configured bounds, a position flag, then cash as a fraction of portfolio value. `KDR_STATE_VECTOR_SCHEMA` gates the layout: `normalized` (default) emits the scaled features, `legacy` reproduces the raw pre-normalization features (price, holding, sentiment, cash) so a run under either flag can be compared feature by feature. `legacy_vector` and `normalized_vector` are also callable directly when a caller wants both layouts from one state.

### 2. Decision Engine & Reinforcement Learning (`src/model.py`)
- `BellmanEngine`: Scores every candidate action as `r + γ·V(s')` and returns the argmax, so `discount_factor` shapes the decision; `compute_target_q` is the same expression the training loop fits.
- `LinearValueFunction`: The concrete TD(0) value function `V(s) = w·s + b`; `update` moves the weights toward a Bellman target by the learning rate.
- `RLTrainer`: TD(0) training loop that asks the engine for a target and hands it to the value function.
- `ValueFunction`: Protocol defining state value estimation; `LinearValueFunction` satisfies it.

The engine is handed two functions rather than a policy: `transition(action, state)` returns the immediate reward and the successor state, and `value_fn(state_vector)` estimates a successor's value. The caller therefore owns the reward and transition model, and the engine owns the maximization.

### 2b. Backtesting (`src/backtest.py`)
`Backtester` replays stored bars from PostgreSQL through the same state, action space and engine the live system uses, as a paper portfolio that never touches the broker. `run()` returns the decision count, starting and ending portfolio value, return, mean reward and one entry per decision; `summarize()` renders the one line the CLI prints. Run it with `BACKTEST_SYMBOLS=AAPL,TSLA python -m src.backtest`, optionally setting `BACKTEST_CASH` and `BACKTEST_MINUTES`. `_transition` is the harness's reward model: the signed price change the action captured, with a BUY spending `position_weight` of cash and a SELL releasing the same fraction.

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
