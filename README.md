# KDR

A Python 3.14-powered trading engine that leverages a **Bellman decision model** to optimize equity portfolio actions (Buy, Sell, Hold) using real-time market data, volatility, and sentiment indicators.

---

## Features

- **Bellman Decision Engine:** Scores every candidate trade movement as immediate reward plus discounted future value (`r + γ·V(s')`) and acts on the argmax, so the discount factor shapes decisions; a value-function training loop fits `V` to those targets.
- **The Live Path Decides With That Model:** The ingestion pipeline hands every bar it maps a `FullState` to `TradingExecutor.decide`, so the decision the dashboard shows is the model's argmax, not a momentum heuristic; a tie with `HOLD` decides `HOLD`.
- **Normalized State Vector:** Log price changes, sentiment rescaled onto [-1, 1], position flags and cash as a fraction of portfolio value, so one value function serves symbols priced from a cent to six figures; `KDR_STATE_VECTOR_SCHEMA=legacy` reproduces the old raw layout for comparison.
- **Backtesting & Training Harness:** Replays stored bars through the live decision path as a paper portfolio and reports the return, the mean reward and every decision (`BACKTEST_SYMBOLS=AAPL,TSLA python -m src.backtest`). The same walk also fits the value function (`BACKTEST_TRAIN_EPOCHS=3`), and the trained weights are stored in the `model_weights` table for the next process to load.
- **Risk Management:** Dynamic position sizing powered by the Kelly Criterion with a strict 20% portfolio capital allocation cap.
- **Real-Time Data Ingestion:** Live streaming trade and bar WebSocket ingestion via Alpaca Markets.
- **Sentiment Analysis:** Multi-source financial sentiment scoring via Adanos API.
- **Persistence:** High-throughput batch persistence with PostgreSQL for minute bars, ticks, sentiment, and model state.
- **Real-Time Dashboard:** Interactive Vue 3 + Chart.js web interface served with FastAPI and WebSockets, supporting multi-timeframe deltas (1h, 24h, 5d, 30d, 1y, YTD) and persistent index caching.
- **Reporting & Observability:** Automated CSV trade logs, equity curves, Plotly charts, and performance metric evaluation.

---

## Requirements

- **Python 3.14+** (Required)
- **PostgreSQL Database**
- **Alpaca Markets Account** (API Key & Secret)
- **Adanos API Account** (Sentiment API Key)

---

## Setup & Installation

### 1. Quick Start

Run the automated startup script, which validates Python 3.14, configures the virtual environment, verifies dependencies, and launches the pipeline:

```bash
# Unix / macOS
./bin/start.sh              # Standard logging
./bin/start.sh --debug      # Verbose debug logging

# Windows (cmd.exe)
bin\start.bat               # Standard logging
bin\start.bat --debug       # Verbose debug logging
```

### 2. Manual Setup

```bash
# 1. Create and activate a Python 3.14 virtual environment
python3.14 -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# Edit .env with your credentials
```

### 3. Database Initialization

Ensure PostgreSQL is running and create the database:

```bash
createdb kdr_db
# Or via SQL:
psql -U postgres -c "CREATE DATABASE kdr_db;"
```

Tables are automatically initialized on startup via `db_manager.connection.init_db()`.

---

## Configuration

Configure `.env` in the repository root:

| Variable | Required | Description |
| :--- | :--- | :--- |
| `ALPACA_API_KEY` | Yes | Alpaca Markets API key identifier |
| `ALPACA_SECRET_KEY` | Yes | Alpaca Markets secret API key |
| `ALPACA_BASE_URL` | No | Alpaca API endpoint (defaults to paper trading: `https://paper-api.alpaca.markets`) |
| `ADANOS_API_KEY` | Yes | Adanos sentiment analysis API key. Read when the sentiment adapter is used rather than at import time; if it is missing, sentiment lookups are skipped and the pipeline continues with neutral sentiment. |
| `DATABASE_URL` | Yes | PostgreSQL connection string (`postgresql://user:pass@localhost:5432/kdr_db`) |
| `QA_DATABASE_URL` | No | Alternate database used when `SKIP_DB_TEST` is set or `USE_QA_DB` is true. Set it so test traffic cannot reach a live book. |
| `KDR_STATE_VECTOR_SCHEMA` | No | `normalized` (default) or `legacy`. Chooses the state vector layout the model trains and decides on; `legacy` reproduces the pre-normalization features so a run under either flag can be compared. |
| `BACKTEST_SYMBOLS` | No | Comma-separated symbols `python -m src.backtest` replays. Required for that command; ignored elsewhere. |
| `BACKTEST_CASH` | No | Paper portfolio's starting cash for the backtest (default `100000`). |
| `BACKTEST_MINUTES` | No | Window of stored bars the backtest replays, in minutes (default `1440`). |
| `BACKTEST_TRAIN_EPOCHS` | No | Times to fit the value function to that same window after reporting the backtest (default `0`, meaning no training). A trained pass stores the weights in the `model_weights` table under the symbol list the state vector covers, which is what the pipeline loads. |

Nothing in the source carries a database address of its own. If neither `DATABASE_URL` nor the QA override resolves, `get_database_url` logs an error and returns an empty string, and connecting raises rather than silently writing somewhere else.

### Time handling

Every timestamp the system records is UTC. `src/times.py` is the single place that reads or writes one, so the rule is stated once instead of at each call site:

| Helper | Purpose |
| :--- | :--- |
| `now_utc()` | Current time as a timezone-aware UTC `datetime` — use this instead of `datetime.now()` or `time.time()` |
| `to_utc(value)` | Accepts a `datetime` (naive is read as UTC), a numeric epoch in seconds, milliseconds or nanoseconds, or ISO-8601 text, and returns a UTC-aware `datetime` |
| `parse_iso(text)` | ISO-8601 text, read as UTC when it carries no offset |
| `epoch_seconds(value)` | The float seconds value to store or compare against |
| `from_epoch(seconds)` | Inverse of `epoch_seconds` |
| `hhmmss(value)` | `HH:MM:SS` display text in UTC |
| `epoch_minute(value)` | Minutes since the epoch — the key one-minute bars are bucketed by |
| `minute_key(value)` | `YYYY-MM-DD HH:MM` minute key for market data |

Stored timestamps are UTC-aware `datetime` values, so PostgreSQL keeps the offset and comparisons stay correct across DST and timezones. Numeric epoch values keep their precision: a nanosecond timestamp is not silently truncated to seconds.

### State vector

`FullState.to_vector(symbols)` flattens a state to `3 * len(symbols) + 1` features, in the order the symbols are passed. By default every feature is scaled, so one value function can serve a symbol priced at a cent and one priced at sixty thousand:

| Feature (per symbol) | Value |
| :--- | :--- |
| Price movement | `log(price / previous_price)`, or `0` when there is no previous price |
| Sentiment | Score rescaled onto roughly [-1, 1] using `MarketState.sentiment_min`/`sentiment_max` (Adanos reports 0 to 1) |
| Position | `1` when the symbol is held, `0` when flat |
| Cash (final entry) | Cash as a fraction of the portfolio's total value (cash plus everything held) |

Set `KDR_STATE_VECTOR_SCHEMA=legacy` to get the pre-normalization layout instead — raw price, raw holding, raw sentiment, then raw cash — which is the same length, so an old and a new run can be compared feature by feature. `state.legacy_vector(symbols)` and `state.normalized_vector(symbols)` return either layout on demand regardless of the flag. An unrecognized value raises at the first vectorization rather than silently picking one.


### Verification Utilities

Check environment variables and system readiness:

```bash
# Inspect environment variable status (values masked)
python bin/check_env.py

# Run pre-flight startup validations
./tests/test_startup.sh
```

---

## Execution & Operations

### Ingestion Pipeline & Decision Engine

Run the primary data ingestion and trading engine:

```bash
# Continuous streaming with debug output
python src/scraper.py --debug

# Stream specific symbols
python src/scraper.py --symbols AAPL,MSFT,GOOGL

# Run for a bounded duration (e.g., 60 seconds)
python src/scraper.py --duration 60

# Run a single batch pass
python src/scraper.py --no-continuous
```

The pipeline decides with the model. Every bar it maps is turned into a `FullState`
(`StateMapper.build_decision_state`: the bar's close measured against its open,
neutral sentiment when Adanos has nothing to report, and the portfolio the decision
belongs to) and scored by `TradingExecutor.decide`, which takes the argmax of
`r + γ·V(s')` over `BUY`, `SELL` and `HOLD` for that symbol. The dashboard's
decision row is that result, with the winning score and the margin over the
runner-up as its confidence. A writer built without an executor — a test, a
notebook — keeps the momentum heuristic instead, so the two paths stay visibly
different. The value function it scores with is the one a previous training pass
stored in the `model_weights` table for the symbols being traded; with nothing
stored the process starts untrained, which means scoring on the immediate reward.

### Backtesting

Replay stored bars through the decision model as a paper portfolio. This is the
spec's validation step: the harness drives the same state, action space and engine
the live system uses, on data whose outcome is already known, and it never touches
the broker.

```bash
# Replay the last 1440 minutes of stored bars for two symbols
BACKTEST_SYMBOLS=AAPL,TSLA python -m src.backtest

# Wider window and a different starting portfolio
BACKTEST_SYMBOLS=AAPL,TSLA BACKTEST_MINUTES=10080 BACKTEST_CASH=250000 python -m src.backtest

# Same history, then fit the value function to it and store the weights
BACKTEST_SYMBOLS=AAPL,TSLA BACKTEST_TRAIN_EPOCHS=3 python -m src.backtest
```

The command prints a one line summary (`steps decisions, start -> end (+/-return)`)
and the full JSON report: decision count, starting and ending portfolio value,
return, mean reward, and one entry per decision with the timestamp, symbol,
movement, reward and portfolio value after the move. With `BACKTEST_TRAIN_EPOCHS`
set it prints a second report — epochs, steps and the mean reward of the last one —
and writes the fitted parameters to the `model_weights` table.

One walk of the history answers both questions: `Backtester.replay` returns the
decisions and the `(state vector, reward, next state vector)` triples, `run()`
turns the decisions into the report above and `train()` fits one TD(0) pass per
epoch over the triples. The reward model is `portfolio_transition` in `src/model.py`
— the same function the live path scores with — and `load_value_function` reads
stored weights back, dropping a record whose vector length does not match the
symbols it was asked about rather than scoring against it.

### Background Service Management

Use the helper scripts in `bin/` to manage background execution:

```bash
# Start pipeline in the background
./bin/local-start.sh

# Monitor background logs
tail -f logs/kdr.log

# Gracefully stop the background pipeline
./bin/local-stop.sh
```

The four scripts answer different questions for a user — `bin/start.sh` bootstraps
the environment and runs the dashboard in the foreground, `bin/daemon.sh` controls
a background service (`start`/`stop`/`restart`/`status`), `bin/local-start.sh` runs
one pass locally, `bin/local-stop.sh` stops what is running — but the mechanics of
putting the pipeline in the background are one thing, in `bin/_start_common.sh`. All
four source it, so they agree on the PID file (`logs/kdr.pid`), the interpreter
(`.venv/bin/python`), the argument parser and the pre-flight import: a run started
by any of them is stopped cleanly by any of the others, and an import failure is
reported where the user is standing rather than lost to the rotating log.

The pipeline writes one log file, `logs/kdr.log`, that rotates at 10 MB and keeps
five backups, so a service left running for weeks cannot fill the disk with a
per-run file. The rotation backups (`logs/kdr.log.*`) are gitignored, and the deploy
script excludes the whole `logs/` directory rather than only `logs/*.log`.

### Real-Time Web Dashboard

Start the FastAPI web server to access the live trading dashboard:

```bash
python -m uvicorn src.web_server:app --host 0.0.0.0 --port 8001
```

Once running, navigate to:
- Dashboard: `http://localhost:8001/kdr/`
- API Index: `http://localhost:8001/kdr/api/market-indexes`
- Status: `http://localhost:8001/kdr/api/status`

---

## Testing

Run the full automated test suite:

```bash
# Run the collected suite (pytest.ini matches test_*.py and *_test.py)
pytest tests/

# Database integration tests: real SQLAlchemy, no mocks. The postgres tier
# self-skips unless USE_QA_DB (or SKIP_DB_TEST) is set and a server is reachable.
pytest tests/unit/test_database_integration.py
USE_QA_DB=1 pytest tests/unit/test_database_integration.py

# Pipeline contract tests: domain, model, risk sizing, execution, stream -> DB
pytest tests/test_integration_pipeline.py

# Run test suite with code coverage
pytest tests/ --cov=src --cov-report=term-missing
```

The whole `tests/` tree is collected and runs in seconds. Two fixtures in
`tests/conftest.py` keep it that way without a live system: a bounded probe of
the configured database gates the server and websocket modules (they skip when
the DB is unreachable), and when it is unreachable the web server's DB accessor
is stubbed to `None`, which is the same state the app reaches when database
construction fails. To exercise the skipped tests, start the app and point
`DATABASE_URL` at a reachable host.

---

## Linting & Type Checking

`pyproject.toml` carries the committed ruff and mypy configuration, so a reviewer
and CI get the same result:

```bash
ruff check src/ tests/    # 0 findings
ruff check src/ tests/ --fix
mypy src/                 # clean; plotly and the Alpaca client ship no stubs
```

ruff is a development tool, not a runtime dependency, so it is not in
`requirements.txt`:

```bash
.venv/bin/python -m pip install ruff
```

Two rules are worth naming because they shape the code rather than report on it:
the `C` rules reject a function complex enough to need splitting, and every
`except` names the failure it answers (`DB_FAILURES`, `PAYLOAD_FAILURES`,
`BROKER_FAILURES`) instead of catching `Exception`. The few broad catches that
remain sit at thread and process boundaries and each carries a comment saying why.

---

## Troubleshooting

| Issue | Cause | Resolution |
| :--- | :--- | :--- |
| `Python 3.14 is not installed` | Missing or mismatched Python version | Install Python 3.14 (`brew install python@3.14`) and ensure `.venv` uses `python3.14`. |
| `ModuleNotFoundError: No module named 'src'` | Project root not in `PYTHONPATH` | Run using `./bin/start.sh` or export `PYTHONPATH="${PYTHONPATH}:$(pwd)"`. |
| `AuthenticationError` | Invalid Alpaca or Adanos credentials | Verify keys in `.env` or run `python bin/check_env.py`. |
| `DatabaseConnectionError` / `pg_isready` failure | PostgreSQL service is offline | Verify PostgreSQL is running (`brew services start postgresql`) and check `DATABASE_URL`. |
| `RateLimitError` | API rate limit exceeded | Back off polling frequency or check subscription tier limits. |

---

## Directory Structure

```text
├── bin/                 # Process management and deployment scripts (start.sh, daemon.sh, deploy_rpi4.sh)
│   └── _start_common.sh # The one start/stop library the four entry scripts source
├── docs/                # Architecture, deployment, and specification documentation
│   ├── architecture.md  # Detailed system architecture and component designs
│   ├── deployment.md    # Nginx reverse proxy and Cloudflare Tunnel setup
│   ├── plans/           # Work-in-progress improvement plans and TODO checklists
│   └── specs/           # Feature specifications (Bellman decision model)
├── src/                 # Core engine source code
│   ├── domain.py        # Pydantic schemas (MarketState, PortfolioState, Action)
│   ├── model.py         # Bellman engine, the shared reward model, and the training loop
│   ├── backtest.py      # Historical replay, paper portfolio, and the training pass
│   ├── trading_execution.py # Risk management, Kelly sizing, and Alpaca execution
│   ├── scraper.py       # CLI for the pipeline; re-exports the modules below
│   ├── stream_client.py # Alpaca WebSocket streaming (AlpacaStreamProcessor)
│   ├── data_buffer.py   # Minute-bucketed tick aggregation (DataStreamBuffer)
│   ├── db_writer.py     # Queue draining, DBWriterWorker, run_worker_threads, live decisions
│   ├── logging_config.py # The one logging configuration (rotating logs/kdr.log)
│   ├── database.py      # SQLAlchemy persistence, schema migrations, stored weights
│   ├── state_mapper.py  # Tick/bar → domain states, incl. the state the model decides on
│   ├── sentiment_adapter.py # Adanos sentiment integration
│   ├── web_server/      # FastAPI REST & WebSocket server, as a package
│   │   ├── data_layer.py  # buffers, the index store, database access
│   │   ├── api_layer.py   # the routes and the WebSocket endpoint
│   │   └── __init__.py    # re-exports, so src.web_server:app keeps working
│   ├── reporting.py     # CSV, metrics, and Plotly equity chart generators
│   └── static/          # Web dashboard (Vue 3 + Chart.js)
├── tests/               # Unit and integration test suites
│   ├── conftest.py      # Database reachability gate shared by all tests
│   ├── test_integration_pipeline.py # Pipeline contract tests (domain → DB)
│   └── unit/            # Component unit tests
├── requirements.txt     # Python package dependencies
├── pyproject.toml       # Committed ruff + mypy configuration
└── AGENTS.md            # Agent instructions and context guidelines
```

---

## Additional Documentation

- [System Architecture](docs/architecture.md): In-depth component workflows and performance design.
- [Deployment Guide](docs/deployment.md): Instructions for Nginx reverse proxy and Cloudflare Tunnel deployments.
- [Bellman Decision Spec](docs/specs/bellman_decision.md): Mathematical and functional specification for the core model.
- [Improvement Plan](docs/plans/2026-10-11-improvements-plan.md): Review findings and phased fix plan.
- [TODO Checklist](docs/plans/2026-10-11-todo.md): Actionable list tracking each planned fix.
