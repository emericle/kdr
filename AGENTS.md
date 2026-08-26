# Agents Instructions

This file contains context-rich, high-signal instructions to help agents navigate this repository efficiently.

## Core Context
- **Domain:** Financial Market Trading.
- **Technology:** Python 3.14+, PostgreSQL, Alpaca, Adanos.
- **Key Logic:** Employs a Bellman Decision model to manage an equity portfolio.

## Project Structure & Entrypoints
- **Scraper/Data Ingestion:** `src/scraper.py` is the primary entry point for data collection and ingestion.
- **Database:** Schema and interactions are managed in `src/database.py`.
- **Gatekeeper:** `src/config_gatekeeper.py` validates all system configurations before execution.
- **Models:** Core logic and weights are handled in `src/model.py`.

## Development & Execution
- **Running the Processor:** Use `python src/scraper.py --debug` to run the ingestion pipeline with verbose logging.
- **Testing:** All unit tests are located in the `tests/` directory. Run via `python -m pytest tests/`.
- **Database Init:** The system initializes the database on startup via `db_manager.connection.init_db()`. Ensure the `DATABASE_URL` is correct in `.env` before the first run.

## Specific Quirks & Rules
- **Python 3.14:** The application explicitly checks for version 3.14. Ensure the environment is correctly configured as `python3.14`.
- **Thread Safety:** The `DataStreamBuffer` in `src/scraper.py` uses a thread-safe lock for managing tick buffers.
- **Environment:** The `.env` file is critical. It contains Alpaca, Adanos, and Database credentials.
- **Execution Flow:** Data flows from **Alpaca Stream** -> **Buffer** -> **DBWriterWorker** -> **PostgreSQL**.
