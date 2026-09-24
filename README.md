# kdr

First AI driven development project. Create a Bellman decision model to simulate equities purchasing decisions.

# Financial Model - Bellman Decision & Multi-Asset Trading

A Python 3.14-powered trading engine that leverages a Bellman decision model to determine optimal actions (Buy, Sell, Hold) for equity assets based on market sentiment, volatility, and technical indicators.

## Features

- **Decision Engine:** Built with Gymnasium and RL models.
- **Risk Management:** Dynamic position sizing using the Kelly Criterion with a 20% capital cap.
- **Data Integration:** Real-time tick ingestion from Alpaca and Sentiment analysis via Adanos.
- **Persistence:** Powered by PostgreSQL to store historical bars, sentiment scores, and persistent model weights across sessions.

## Requirements

- Python 3.14+ (REQUIRED)
- PostgreSQL Database
- `alpaca-trade-api` (Alpaca API)
- Adanos (Sentiment Analysis)

## Setup

1. **Clone the repository** (if not already cloned)
2. **Python 3.14+** is required (Check with `python3.14 --version`)
3. **Quick Start:** Use the startup scripts instead of manual setup:
   ```bash
   # Run with normal logging
   ./start.sh

   # Run with debug mode (verbose logging)
   ./start.sh --debug
   ```

4. **Manual Setup (if needed)**:
   - Create the virtual environment: `python3.14 -m venv .venv`
   - Activate the environment: `source .venv/bin/activate`
   - Install dependencies: `pip install -r requirements.txt`
   - Configure `.env` with the following variables:
     - `ALPACA_API_KEY`
     - `ALPACA_SECRET_KEY`
     - `ADANOS_API_KEY`
     - `DATABASE_URL` (or separate connection credentials)

5. **Database Setup**:
   - Ensure PostgreSQL is running
   - Create a database: `createdb kdr_db` or via SQL
   - Set the DATABASE_URL in your .env file

## Directory Structure

- `/src`: Core logic (TradingEnv, Data Ingestion, Model Weights)
- `/outputs`: Automated reports (CSV and Plotly charts) categorized by date.
- `/tests`: Unit and integration tests.

## Execution

Run the system using:

```bash
python src/scraper.py --debug
```

### Available Flags

| Flag | Description |
| :--- | :--- |
| `--debug` | Enables verbose logging and debug output. |

Note: Running `python src/scraper.py --help` will display all available command-line options.

## Startup Utilities

The project includes several utility scripts to help with setup and validation:

| Script | Description |
| :--- | :--- |
| `start.sh` / `start.bat` | Main startup script (checks env vars, sets up venv, runs process) |
| `check_env.py` | Displays status of all environment variables |
| `test_startup.py` | Validates system setup before starting the process |

### Using the Utilities

**Quick Environment Check:**
```bash
python check_env.py
```

**Validation Tests:**
```bash
python test_startup.py
```
