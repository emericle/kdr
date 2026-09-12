# Handoff Document

## Context Summary
The project implements a **Bellman Decision model** for equity portfolio management within a trading system. The system utilizes Python 3.14+, PostgreSQL, Alpaca, and Adanos, following a structured test-driven development workflow.

## Progress to Date
- **Domain Models & Math Engine**:
  - `src/domain.py`: Pydantic models for `MarketState`, `PortfolioState`, `FullState`, `Action`, and `TradeMovement`.
  - `src/model.py`: `BellmanEngine`, `RLTrainer`, and `ValueFunction` protocol.
- **Data Pipeline & Adapters**:
  - `src/state_mapper.py`: `StateMapper` mapping tick data to domain states.
  - `src/sentiment_adapter.py`: `AdanosSentimentAdapter` interfacing with Adanos API.
  - `src/data_adapters.py`: Market and sentiment data adapters.
  - `src/scraper.py`: `DataStreamBuffer`, `DBWriterWorker`, and `AlpacaStreamProcessor`.
- **Trading Execution & Risk Management**:
  - `src/trading_execution.py`:
    - `PositionSizing`: Kelly Criterion sizing and share calculation with capital limits.
    - `RiskManager`: Portfolio position exposure caps (20%), cash allocation checks, and short/holding constraints.
    - `OrderValidator`: Validates symbol, side, and quantity.
    - `OrderTracker`: Records fills, tracks order history, and computes net positions.
    - `AlpacaClient`: REST interface for order placement, account queries, and position retrieval.
    - `TradingExecutor`: Connects Bellman decision outputs (Action/TradeMovement) through RiskManager and submits orders via AlpacaClient.
- **Observability & Reporting**:
  - `src/reporting.py`:
    - `CSVReporter`: Generates CSV logs for executions and portfolio values.
    - `ChartGenerator`: Generates HTML/Plotly charts for equity curves against benchmarks.
    - `PerformanceAnalyzer`: Calculates win rate, total return, profit factor, average win/loss.
    - `ReportScheduler`: Schedules automated reporting tasks.
    - `MultiSymbolReporter`: Generates multi-symbol summary reports.
- **Testing & Verification**:
  - 163 unit tests passing across all components (`tests/unit/` and `tests/`).
  - Integration tests passing in `tests/integration_tests.py`.
  - Code coverage at 82%.

## Status
- **Risk Management (Kelly Criterion & dynamic position sizing)**: [COMPLETED]
- **Alpaca Order Integration & Trading Execution**: [COMPLETED]
- **Observability & Automated Reporting Engine**: [COMPLETED]
- **Next Objective**: Live websocket connectivity in `AlpacaStreamProcessor` (`src/scraper.py`) for live streaming ticks into `DataStreamBuffer`.

## Files & References
- Trading Execution: `src/trading_execution.py`
- Reporting: `src/reporting.py`
- Domain Logic: `src/domain.py`
- Bellman Model: `src/model.py`
- Ingestion & Buffer: `src/scraper.py`
- Unit Tests: `tests/unit/test_trading_execution.py`, `tests/unit/test_reporting.py`, `tests/unit/test_model.py`, `tests/unit/test_domain.py`, `tests/unit/test_database.py`, `tests/unit/test_scraper.py`
- Integration Tests: `tests/integration_tests.py`
