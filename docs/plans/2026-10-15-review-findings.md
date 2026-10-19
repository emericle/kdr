# Review Findings — KDR (2026-10-15)

Second-pass review of `src/`, `bin/` and `src/static/` against `docs/architecture.md`.
The 2026-10-11 plan is already fully implemented; nothing below repeats it. Every
finding was checked against the code, not the docs. Companion checklist:
[TODO](2026-10-15-todo.md).

Three axes: **Persistence** (the data-preservation requirement), **Performance**,
**User experience**, plus a short **Hygiene/correctness** batch.

## Persistence — upgrades must migrate, not drop the database

The requirement: market data (`market_data`, `sentiment_data`, `model_weights`)
must survive schema upgrades, and the store should be kept for as long as possible.

- **No migration path exists.** `DatabaseConnection.init_db()` (`src/database.py:99-107`)
  is `Base.metadata.create_all(engine)`, which creates tables that are *missing* and
  does nothing to tables that already exist. It never adds a column, never changes a
  type, never migrates. So the only way to apply a schema change today is to drop the
  database and recreate it — which deletes every stored bar. There is no Alembic in
  `requirements.txt` and no `alembic/` directory anywhere.
- **The README already claims migrations that do not exist.** `README.md:363` labels
  `database.py` as "SQLAlchemy persistence, **schema migrations**, stored weights" —
  there are none. Doc drift, and it hides the gap.
- **The deploy is safe but the check script is not.** `bin/deploy_rpi4.sh` rsyncs code
  and never touches the PostgreSQL store (it lives on `pinky.local`), so a redeploy
  already preserves data — good. But `bin/check-database.sh:70-78` runs `init_db()`
  on every invocation, and `:55` calls `db.connection.get_engine()`, a method that
  does not exist on `DatabaseConnection` (it has `engine`, `get_session`, `init_db`,
  `close`). The connection test therefore always raises `AttributeError`, is swallowed
  by the bare `except Exception`, and the script reports "connection failed" and exits
  1 — it can never reach the `init_db()` step, and if it could, re-running it must not
  be destructive.
- **Retention is unbounded but undocumented.** `market_data` grows forever (the
  `(symbol, timestamp)` unique index stops duplicates, which is good). Nothing prunes
  it, which is what "as long as possible" wants — but there is no statement of the
  retention policy, no index-growth note, and no guardrail asserting that no code path
  drops the store.

## Performance

- **`_interval_price` still costs 1–3 queries per duration** (`src/database.py:528-550`).
  `get_prices_at_interval_starts:513-515` loops the six durations and calls it each, so
  a cold symbol pays 1 anchor + 6–18 round-trips. Fix: one range query per symbol from
  `min(target_time)` across all durations, ordered ascending, then pick each duration's
  boundary in Python with `bisect`. Cuts 7–19 round-trips to 2.
- **The per-minute interval cache is cleared on every write** (`src/database.py:274`).
  Bars are written ~once/minute/symbol, so the dashboard's 1 Hz broadcast almost always
  misses the cache. The minute key already bounds staleness — drop the unconditional
  `clear()` (or clear only the durations whose window the new bar falls in).
- **`get_summary` pays the interval read twice** (`src/web_server/data_layer.py:593`
  `_interval_starts`, then `_change_for_duration:503-509` re-queries
  `get_price_at_interval_start`). Pass the already-fetched `starts` dict down and reuse it.
- **`compute_recommendation` pays it a third time** (`data_layer.py:779` calls `get_summary`
  again); `broadcast_loop` (`api_layer.py:94-95`) already has the summary — take it as a
  parameter.
- **O(N) scans over up to 500 ticks per call** (`data_layer.py:386/412/587` use
  `max(reversed(history), key=...)` and `min(history, ...)`). The deque is append-ordered,
  so `history[-1]`/`history[0]` are O(1). `fetch_tracked_indexes:246` runs this for 5
  indexes × up to 6 candidates every second.
- **Three full passes over `history` plus a 500-element allocation per `get_summary`**
  (`data_layer.py:598-601`), once per symbol per second. Track running high/low/volume in
  `add_tick`.
- **The index cache is written inside the lock on every call** (`data_layer.py:273`
  `update_index` does `os.makedirs` + `json.dump` per call, reached per tick and 5×/second
  from `fetch_tracked_indexes`). Set a dirty flag and flush on a timer or at shutdown.
- **Dashboard HTML is `stat`+`open`+`read` per request** (`api_layer.py:161-164` and
  `:169-172`, and `/symbol/{symbol}` is hit per symbol). Cache the bytes at import or use
  `FileResponse`.
- **`broadcast_loop` computes every symbol summary before checking for clients**
  (`api_layer.py:87-99`); with zero sockets it still pays N symbols × interval queries
  every second. Bail when `active_connections` is empty.
- **A new Alpaca `REST` client is constructed every 3 s** (`stream_client.py:265-266` from
  `_flusher_loop:287`); the SDK constructor does a credentials round-trip. Build it once in
  `__init__`.
- Judgement calls: `data_buffer.py:68/78` raw tick lists are write-only in production and
  retain every tick until the minute rolls (cap with `deque(maxlen=…)` or drop);
  `database.py:358-371` does two `to_utc` normalizations + a `strftime` per row ×1000 rows
  per `/api/symbols/{symbol}`; `api_layer.py:306` `_merge_ticks` re-sorts two already-sorted
  lists (linear merge instead); `data_layer.py:392/674` `list(history)[-limit:]` copies the
  whole deque before slicing.

## User experience

- **Fabricated market-index numbers render as live data.** `data_layer.py:129-180` seeds
  VIX 15.25 / DJIA 43910.50 with `lastUpdate: "16:00:00"`, and `dashboard.html:1045-1050`
  duplicates the same seed. On a cold start the top row shows five plausible, wrong prices
  before any tick arrives, and `update_index` (`data_layer.py:273`) persists those defaults
  to `index_cache.json`, so they later look identical to real values. The server already
  emits `priceType` (`:290`) — the dashboard never reads it. Fix: seed with
  `currentPrice: null`, `priceType: "placeholder"`, and render "awaiting first tick".
- **Staleness is invisible.** `dashboard.html:820` prints `Last update: 16:00:00` (hh:mm:ss
  only). A value carried over from a prior session/day reads as current. Show date or
  relative age, and add a `stale` class when `priceType !== "live"` or age > session length.
- **The duration filter lies about its window.** `dashboard.html:1536-1538`: when the
  requested window has no ticks it falls back to `hist.slice(-100)`, so "1 Hour" shows ~30
  days of points on an axis titled 1h. Render an "insufficient data for this window" state
  with the actual span shown.
- **"200-Day MA" is a 200-tick average.** `dashboard.html:1626` labels it 200-Day;
  `calculateMA` (`:1234`) windows 200 tick points, so on a 1h chart it is ~33 minutes.
  `api_layer.py:312-316` is worse: with fewer than `window` ticks it returns the last price,
  so the MA line exactly overlays the price line — a fake confirmation. Label with the
  window actually used, and emit nulls (no line) when points < window.
- **Client-side dishonest decision fallback survives the plan's fix.** `dashboard.html:1165-1174`
  prints "Model evaluating optimal Bellman value function…" and `:874`/`:969` render
  `confidence || 0.7` as a hard 70%. The plan fixed the server strings; these client defaults
  remain. Show "no decision recorded yet" and `—` for missing confidence.
- **Card deltas name no window.** `dashboard.html:851-857`: the summary card's delta changes
  invisibly when the drill-down dropdown changes `selectedDuration` (`:1093`). Label it
  (`24h ▾`).
- Judgement calls: first paint blocks on synchronous DB reads (`api_layer.py:392` builds the
  WS init via `get_summary` → `_interval_starts` synchronously; send a buffer-only init first,
  augment after); frozen cards while disconnected (`dashboard.html:786` says reconnecting but
  cards keep showing last-known prices as if live — add a stale overlay); fixed 380px canvas on
  mobile (`dashboard.html:514` — use `clamp(220px, 45vh, 380px)`).

## Hygiene / correctness

- **`bin/check-database.sh:55` calls `db.connection.get_engine()`, which does not exist.**
  The connection test always raises `AttributeError`, is swallowed by `except Exception`, and
  the script always reports failure and exits 1. Use `db.connection.engine` / `get_session()`.
- **Dead stub `save_to_db`** (`src/db_writer.py:190-195`, body is a comment "Database writing
  logic here..."). Nothing calls it — delete it (Speculative Generality).
- **README `database.py` line claims "schema migrations"** (`README.md:363`) that do not exist —
  fix once the migration task lands.
