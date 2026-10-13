# KDR

A Python 3.14-powered trading engine that leverages a **Bellman decision model** to optimize equity portfolio actions (Buy, Sell, Hold) using real-time market data, volatility, and sentiment indicators.

---

## Features

- **Bellman Decision Engine:** Computes optimal action values with reinforcement learning techniques and Gymnasium-compatible environments.
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
| `ADANOS_API_KEY` | Yes | Adanos sentiment analysis API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string (`postgresql://user:pass@localhost:5432/kdr_db`) |

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

### Background Service Management

Use the helper scripts in `bin/` to manage background execution:

```bash
# Start pipeline in the background
./bin/local-start.sh

# Monitor background logs
tail -f logs/system.log

# Gracefully stop the background pipeline
./bin/local-stop.sh
```

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
├── docs/                # Architecture, deployment, and specification documentation
│   ├── architecture.md  # Detailed system architecture and component designs
│   ├── deployment.md    # Nginx reverse proxy and Cloudflare Tunnel setup
│   ├── plans/           # Work-in-progress improvement plans and TODO checklists
│   └── specs/           # Feature specifications (Bellman decision model)
├── src/                 # Core engine source code
│   ├── domain.py        # Pydantic schemas (MarketState, PortfolioState, Action)
│   ├── model.py         # Bellman decision engine and RL training
│   ├── trading_execution.py # Risk management, Kelly sizing, and Alpaca execution
│   ├── scraper.py       # Alpaca WebSocket streaming, buffer, and batch DB writer
│   ├── database.py      # SQLAlchemy persistence and schema migrations
│   ├── state_mapper.py  # Tick/bar transformation to domain states
│   ├── sentiment_adapter.py # Adanos sentiment integration
│   ├── web_server.py    # FastAPI REST & WebSocket server
│   ├── reporting.py     # CSV, metrics, and Plotly equity chart generators
│   └── static/          # Web dashboard (Vue 3 + Chart.js)
├── tests/               # Unit and integration test suites
│   ├── conftest.py      # Database reachability gate shared by all tests
│   ├── test_integration_pipeline.py # Pipeline contract tests (domain → DB)
│   └── unit/            # Component unit tests
├── requirements.txt     # Python package dependencies
└── AGENTS.md            # Agent instructions and context guidelines
```

---

## Additional Documentation

- [System Architecture](docs/architecture.md): In-depth component workflows and performance design.
- [Deployment Guide](docs/deployment.md): Instructions for Nginx reverse proxy and Cloudflare Tunnel deployments.
- [Bellman Decision Spec](docs/specs/bellman_decision.md): Mathematical and functional specification for the core model.
- [Improvement Plan](docs/plans/2026-10-11-improvements-plan.md): Review findings and phased fix plan.
- [TODO Checklist](docs/plans/2026-10-11-todo.md): Actionable list tracking each planned fix.
