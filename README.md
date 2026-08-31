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

1. Ensure Python 3.14+ is installed on your system.
2. Create the virtual environment: `python3.14 -m venv .venv`
3. Activate the environment: `source .venv/bin/activate`
4. Install dependencies: `pip install -r requirements.txt`
5. Configure `.env` with the following variables:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `ADANOS_API_KEY`
   - `DATABASE_URL` (or separate connection credentials)

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
