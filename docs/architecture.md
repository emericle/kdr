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

## Code quality

`pyproject.toml` is committed, so a reviewer gets the same result CI does:

```bash
ruff check src/ tests/   # 0 findings; the config is in pyproject.toml
mypy src/                # clean; plotly and the Alpaca client have no stubs
```

Two policies are enforced by that config rather than by review comments:

- **No oversized functions.** ruff's `C` rules reject a function whose branching
  is complex enough to need splitting, so the long paths in the pipeline
  (`add_market_data`, `run_worker_threads`, `translate_decision`, the dashboard
  summary) are decomposed into named helpers.
- **No blind excepts.** A handler names the failure it is answering — `DB_FAILURES`
  for "the database did not answer", `PAYLOAD_FAILURES` for a malformed wire
  payload, `BROKER_FAILURES` for a failed round trip. The seven remaining broad
  catches sit at thread and process boundaries (a worker thread's whole loop body,
  the blocking stream loop, the dashboard's start/stop, one websocket connection,
  one broadcast tick) where the alternative is to end the process; each carries a
  comment saying so.

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
- `BellmanEngine`: Scores every candidate action as `r + γ·V(s')` and returns the argmax, so `discount_factor` shapes the decision; `score_actions` returns every candidate's score in candidate order, which is what a caller needs to report how sure it is, and `compute_action` is the argmax of it. `compute_target_q` is the same expression the training loop fits.
- `portfolio_transition`: The environment's reward and successor model — the signed price change an action captured, with a BUY spending `position_weight` of cash and a SELL releasing the same fraction of portfolio value. It lives here rather than in the harness so the live path and the backtest score against one model.
- `LinearValueFunction`: The concrete TD(0) value function `V(s) = w·s + b`; `update` moves the weights toward a Bellman target by the learning rate, and `to_dict`/`from_dict` carry the parameters to and from the `model_weights` table.
- `RLTrainer`: TD(0) training loop; `train_step` is one step and `fit` applies one pass over `(state vector, reward, next state vector)` triples.
- `ValueFunction`: Protocol defining state value estimation; `LinearValueFunction` satisfies it.
- `load_value_function`: Reads the stored parameters for a symbol list, sizing check included — a record whose vector length does not match the layout is dropped rather than scored against, and no record means an untrained function.

The engine is handed two functions rather than a policy: `transition(action, state)` returns the immediate reward and the successor state, and `value_fn(state_vector)` estimates a successor's value. The caller therefore owns the reward and transition model, and the engine owns the maximization.

### 2b. Backtesting & Training (`src/backtest.py`)
`Backtester.replay` walks stored bars from PostgreSQL through the same state, action space and engine the live system uses, and returns both what the policy did (one entry per bar) and the `(state vector, reward, next state vector)` triples a training step fits — one walk answers both questions, so a report and a training pass cannot drift apart. `run()` turns the decisions into the report the CLI prints (decision count, starting and ending portfolio value, return, mean reward, one entry per decision); `train()` applies one TD(0) pass per epoch over the triples, which makes it on policy: the transition a step learns from is the one the current policy took. `summarize()` renders the one line. Run it with `BACKTEST_SYMBOLS=AAPL,TSLA python -m src.backtest`, optionally setting `BACKTEST_CASH`, `BACKTEST_MINUTES` and `BACKTEST_TRAIN_EPOCHS` (default 0); a training pass writes the fitted parameters to the `model_weights` table under the symbol list the vector covers, which is what the live path loads.

### 3. Data Pipeline & Adapters

The pipeline is four modules, and `src/scraper.py` is only the command line that
composes them, so `python src/scraper.py --debug` still works and `from src.scraper
import AlpacaStreamProcessor` still resolves:

- **Stream client (`src/stream_client.py`)**:
  - `AlpacaStreamProcessor`: Subscribes to live Alpaca WebSocket trade and bar feeds, with automated background flushing.
- **Buffer (`src/data_buffer.py`)**:
  - `DataStreamBuffer`: Thread-safe, $O(1)$ running OHLCV state aggregation and fast minute key generation, avoiding per-tick string formatting and $O(5N)$ traversals.
- **DB writer (`src/db_writer.py`)**:
  - `DBWriterWorker`: Background worker draining tick and bar queues in batches for single-transaction persistence.
  - `run_worker_threads`: the thread orchestration the entry point calls. It builds one `TradingExecutor` for the run, whose value function is the parameters `load_value_function` reads from the `model_weights` table for the symbols being traded, so a trained process decides with what it trained.
  - The writer carries that executor and maps every bar through it, so the decision the dashboard shows is the model's argmax rather than a momentum heuristic; a writer built without an executor keeps the heuristic, which makes the two paths visibly different instead of silently different.
- **Logging (`src/logging_config.py`)**:
  - The only module that builds handlers. `configure_logging` is called once, from an entrypoint's `main`, never at import, so importing a module cannot reconfigure global logging or open the log file. Logging goes to one rotating file, `logs/kdr.log`, capped at 10 MB with five backups, rather than a fresh file per run.
- **State Mapper (`src/state_mapper.py`)**:
  - Single-pass transformations mapping incoming raw ticks and bars into structured `MarketState` and `FullState` objects.
  - `build_decision_state` builds the `FullState` the live path hands `TradingExecutor.decide`: the price the bar observed against the baseline the change is measured against (the bar's open when the caller knows it, otherwise the price itself, so no change is invented), sentiment defaulting to the neutral point of the configured scale, and the cash and holdings the decision belongs to.
- **Sentiment & Data Adapters (`src/sentiment_adapter.py`, `src/data_adapters.py`)**:
  - `AdanosSentimentAdapter`: Queries Adanos API for sentiment scores and correlates sentiment with price action.
- **Persistence (`src/database.py`)**:
  - `DatabaseManager`: SQLAlchemy DeclarativeBase models with connection pooling and batched inserts (`session.add_all`) for high-throughput persistence.
  - `.env` is read by `load_environment()` on first use rather than at import, so importing the module for its models no longer picks up credentials as a side effect.
  - Timestamps are stored as UTC-aware values (`DateTime(timezone=True)`), so PostgreSQL keeps the offset and range queries stay correct across DST and timezones. On read, `DatabaseManager` normalizes whatever the row holds back to a UTC-aware `datetime` before comparing it with the caller's boundary.
  - `(symbol, timestamp)` carries a unique index, so a bar a stream reconnect or a re-run hands back is skipped instead of stored twice: the batch is deduplicated in memory, checked against what is already stored with one query, and a batch that loses that check to a concurrent writer is retried one record at a time.
  - Interval-start prices (`1h`, `24h`, `5d`, `30d`, `1y`, `ytd`) are read through `get_prices_at_interval_starts`, which shares one anchor read across every duration for a symbol and caches the result for the minute it was read in; the cache is dropped on every write, so the dashboard never serves a stale anchor.
  - A failed database operation is caught as `DB_FAILURES` (`DBAPIError` plus `InvalidRequestError`), which names "the database did not answer" rather than swallowing any bug in this file.
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
  - `decide(state, symbol)` asks the model: it scores `candidate_actions` for the symbol with `BellmanEngine.score_actions` using `portfolio_transition` as the reward model, and returns the argmax plus a report (the action, every candidate's score, and a confidence that is how far the winner is ahead of the runner-up as a fraction of the whole spread). It places no order and mutates nothing. A state that does not name exactly one symbol raises rather than guessing.
  - A tie with `HOLD` decides `HOLD` in the executor: the engine stays a pure argmax and keeps the earlier candidate, but an indifferent portfolio manager holds rather than trading at the enumeration's accident.
  - `decide_and_execute` runs that decision through `translate_decision`, `OrderValidator` and `RiskManager` and places what survives, sizing against the state the model scored rather than against separate arguments.
  - `execute_decision` moves the executor's own `cash` by what a filled order actually traded, so the next decision is scored against a portfolio that reflects what ran; a rejected order leaves cash alone.

### 5. Web Server & Live Dashboard (`src/web_server/`, `src/static/dashboard.html`)

The server is a package, so `python -m uvicorn src.web_server:app` and
`from src.web_server import market_buffer` keep working against the split:

- **Data layer (`src/web_server/data_layer.py`)**: the thread-safe tick and decision buffers, the persistent index store, and the lazy database handle the dashboard reads.
- **API layer (`src/web_server/api_layer.py`)**: the FastAPI routes (`/api/market-indexes`, `/api/symbols`, `/api/status`) and the real-time WebSocket endpoint (`/ws`).
- **Templates**: static assets in `src/static/` (`dashboard.html`, `test-websocket.html`).
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
