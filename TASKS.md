# Wayfinder Map: Price Display, Index Persistence & Deltas

## Destination
Reliable display and persistence of market index valuations across the top boxes (never $0.00 when markets closed), accurate market-session-aware 24h deltas for symbols and drilldowns, and unified price row formatting (`$<price> +/-$<change> %<changePercent>`) across tickers and individual symbols.

## Notes
- Technology: Python 3.14, FastAPI, Vue 3, Chart.js, PostgreSQL/SQLite
- Assigned build skill: `@implement`
- Rules: Never show $0.00 for index boxes or symbol cards when markets closed; top index summaries use fast persistent cache rather than blocking historical DB queries.

## Tasks Assigned to @implement

### Task 1: Persistent Index Valuations & Fast Retrieval
- **Assignee**: `@implement`
- **Status**: COMPLETED
- **Details**:
  - Persist index valuations (VIX, DJIA, SP500, NASDAQ, RUSSELL2000) to disk (`data/index_cache.json`) with timestamps.
  - On startup, load persisted valuations (fallback to realistic defaults so values are never $0.00).
  - Update persistent store immediately when ticks arrive for any index symbol or proxy in `MarketDataBuffer.add_tick`.
  - In `get_market_index` and `fetch_tracked_indexes`, return the latest value directly from the persistent store without slow/blocking historical database lookups.
  - Include `lastUpdate` in index responses and render "Last update: <time>" in the top index boxes.

### Task 2: Market-Session-Aware 24h Interval Delta Calculation
- **Assignee**: `@implement`
- **Status**: COMPLETED
- **Details**:
  - In `DatabaseManager.get_price_at_interval_start`, detect when the latest available record is older than the interval window (e.g., weekend or market closed).
  - Anchor the lookback interval to the latest open market session rather than wall-clock `now`.
  - Provide fallback database loading in `MarketDataBuffer.get_summary` so symbols like AAPL and TSLA retrieve their latest prices on startup instead of $0.00.
  - In `MarketDataBuffer`, determine `currentPrice` using the latest tick timestamp (`max(history, key=...)`) to avoid out-of-order tick anomalies.

### Task 3: Unified Price Row Display Format
- **Assignee**: `@implement`
- **Status**: COMPLETED
- **Details**:
  - Format price rows across tickers and individual symbols to:
    `$<most recent price for symbol> +/-$<change in price over current period> %<percent change in value>`
  - Update `dashboard.html` for:
    - Top index boxes: display price, signed change (`+/-$<change>`), and percent change (`%...`).
    - Main page symbol cards: add the raw signed change alongside current price and percentage.
    - Symbol drilldown header: display current price, selected duration raw signed change, and percentage change.
  - Color-code changes (green for positive, red for negative).

### Task 4: Test Suite & Verification
- **Assignee**: `@implement`
- **Status**: COMPLETED
- **Details**:
  - Fix existing tick ordering failure in `test_interval_price_change.py`.
  - Add unit tests verifying index persistence, last update display, weekend/closed session deltas, and unified price row formatting.
  - Run full test suite and ensure clean pass.
  - Commit changes to branch and update PR.
