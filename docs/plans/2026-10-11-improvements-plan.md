# Improvement Plan — KDR Code Review Findings (2026-10-11)

Derived from a line-by-line review of `src/` against `docs/architecture.md` and
`docs/specs/bellman_decision.md`. Every finding below was verified against the
code, not the docs. Companion checklist: [TODO](2026-10-11-todo.md).

## Phase 0 — Restore trust in the test suite

Two of the database tests build their expectations out of `MagicMock` chains:
`tests/unit/test_database.py:218` and `:407` set
`mock_session.query.return_value.filter_by.return_value.first.return_value`.
A mock accepts any attribute name and any keyword, so that test stays green
whether the production code calls `filter_by`, `filter`, or a method that does
not exist. The real query path in `get_model_weights` (`src/database.py:354`)
never runs in those tests, which is why the suite cannot be trusted to verify
any database change.

Corrected during implementation: `filter_by` is a real SQLAlchemy 2.0 `Query`
method and `src/database.py:354` works. The earlier reading of it as a
non-existent method was a false positive, and the defect is the test, not the
call site. Confirmed by probe: `q.filter_by(symbol="TSLA").first()` returns the
row, and `q.filter_by(not_a_column=1)` raises `InvalidRequestError`
("Entity namespace for "model_weights" has no property "bogus""), so the real
session does validate column names — a mock cannot.

Two more gaps in the same area, both closed during implementation:

- `tests/integration_tests.py` and `tests/run_tests.py` are never collected.
  `pytest.ini:2` sets `python_files = test_*.py *_test.py`, and neither name
  matches, so the integration tests that exist have not run in CI or locally.
  Replaced by `tests/test_integration_pipeline.py`, a collected pytest module
  with real assertions; the script and the `run_tests.py` path shim are deleted.
- No test drives a real SQLAlchemy session end to end, so nothing pins the
  query API `src/database.py` actually uses.

A third gap found while running the suite: `tests/unit/test_web_server.py` and
`tests/unit/test_websocket_client.py` poll a live uvicorn server with bare
`requests.get` calls and no timeout, and the routes they hit reach the
configured postgres host (`pinky.local`), which resolves over mDNS to several
unreachable addresses — each connect costs ~6.5s. A plain `pytest tests/` run
stalled for minutes. Fixed with a bounded reachability probe in `tests/conftest.py`
that both modules' fixtures request, plus a `timeout=` on every request call.

Approach: keep mocks where they isolate behavior, but add integration tests
that run against the QA database the repo already supports (`SKIP_DB_TEST` /
`USE_QA_DB` in `src/database.py:25`). The QA DB URL is the mechanism already in
place; the gap is that no test uses it.

## Phase 1 — Correctness bugs

| Finding | Location | Fix |
|---|---|---|
| `schema.sql` cannot be loaded by psql: `\i 'market_data' TO ...` is the include-file directive, and `CREATE TABLE IF NOT EXISTS` is MySQL syntax | `schema.sql` | Rewrite as valid psql, or delete — nothing in `src/` or `bin/` references it; SQLAlchemy models are the schema of record |
| Naive vs tz-aware datetimes mixed; DB column is tz-less | `database.py:272`, `scraper.py:111,159,633`, `web_server.py` (many), vs `trading_execution.py:190` | Standardize on UTC; `DateTime(timezone=True)` in `MarketDataModel` (`:84`) |
| Version gate contradicts itself: code requires 3.13, scripts and `AGENTS.md` require 3.14 | `scraper.py:11-12` vs `bin/local-start.sh:9` | Single source of truth — 3.14 per the spec (`docs/specs/bellman_decision.md:15`) |
| QA database URL with a live password committed to source | `database.py:20` | Move to `.env` / `QA_DATABASE_URL` lookup; keep only a placeholder in code |

## Phase 2 — Model gaps: implementation does not meet its own spec

The spec (`docs/specs/bellman_decision.md`) requires Bellman-based decisions, an
RL loop, and backtesting. The code delivers a TD(0) linear value function with a
policy bolted on.

- **Inference never uses the Bellman math.** `BellmanEngine.compute_action`
  (`src/model.py:31`) vectorizes the state and delegates straight to `policy_fn`.
  `compute_target_q` and `discount_factor` are touched only in
  `RLTrainer.train_step` (`model.py:60-76`). There is no max-over-actions.
  Decision: score each `TradeMovement` candidate as `r + γ·V(s')` and take the
  argmax, so `discount_factor` actually shapes decisions.
- **State vector is unnormalized.** `FullState.to_vector` emits raw prices
  (BTC ≈ 60,000 vs AAPL ≈ 230) plus raw cash into a linear value function — one
  feature dominates every gradient step. Switch to log-returns / percent-change
  per symbol, z-scored sentiment, cash as a fraction of portfolio value.
- **`stable-baselines3` and `gymnasium` are in `requirements.txt` but never
  imported.** Either drop them or use them for a real RL loop.
- **Backtesting (spec item 5) does not exist.** No validation harness for the
  model's decisions. This is the largest unmet requirement.
- **`src/utils.py` is empty (0 lines).** Fill or delete.

## Phase 3 — Performance

- **N+1 query pattern.** `get_price_at_interval_start` (`database.py:260`) runs
  2+ sequential queries per call, and the dashboard calls it per symbol × per
  duration (`1h/24h/5d/30d/1y/ytd`). That is N×D×2 round-trips per refresh.
  Batch to one query per symbol across durations, or serve from the in-memory
  `IndexStore` the web server already maintains.
- **No uniqueness on `market_data`.** `MarketDataModel` (`database.py:79-89`) has
  no constraint on `(symbol, timestamp)`, so re-runs and stream reconnects insert
  duplicate bars that pollute every delta and volume calculation. Add a unique
  constraint and an insert-then-skip path.
- **`logs/` is 390 MB across 47 files with no rotation.** Use
  `RotatingFileHandler` where `scraper.py:65` configures logging.

## Phase 4 — Structure and hygiene

- **`web_server.py` is 1,250 lines in one file** (buffers, index store, REST
  routes, HTML dashboard). Split into a package: data layer, API layer,
  templates. Same for `scraper.py` (740 lines).
- **Import-time side effects in `database.py`:** `load_dotenv()` (`:18`) and
  `DATABASE_URL = get_database_url()` (`:33`) run at import, which is why the
  tests mock so heavily. Move to a cached accessor.
- **`logging.basicConfig` called at import in two modules** (`database.py:15`,
  `scraper.py:65`) — the second is a silent no-op. Configure once in the
  entrypoint.
- **45 `except Exception` blocks across `src/`.** Several swallow and continue;
  that is how a database error would have stayed invisible even with real tests,
  and it is the same reason the mocked tests could not see the query API.
  Narrow to specific types; keep broad catches only at thread/process boundaries.
- **Tracked junk in git:** `.coverage` and `tests/unit/test_web_server.py.bak`.
- **No `pyproject.toml`** despite a `.mypy_cache` existing — type/lint settings
  are not committed and do not survive across machines or CI.
- **Two virtualenvs at the repo root:** `.venv` (1.2 GB, created Sep 4, stale)
  and `venv` (93 MB, created Sep 6, the one the scripts activate). Both are
  Python 3.14.7. Consolidate on one name so the stale 1.2 GB is not mistaken
  for the live interpreter. (`venv/` is already self-ignored by its own
  `venv/.gitignore`; `.gitignore:8` covers only `.venv/`.)
- **`test` is a symlink to `tests`**, not a second test tree — worth knowing
  before grepping for test files.

## Phase 2b — The model in the live path (found while implementing Phase 2)

Phase 2 made the model correct in isolation, and the review found that the running
system never consults it: the pipeline and the execution path decided from
heuristics, and nothing trained or loaded the weights the spec's loop produces.
Both halves are closed together, because a model that is never fit and a model
that is never asked are the same gap seen from two sides:

- One reward and successor model (`portfolio_transition` in `src/model.py`) for
  the live path and the backtest, so a decision and a replay cannot disagree about
  what a bar was worth.
- `TradingExecutor.decide` scores the action space and returns the argmax with the
  scores and a confidence; `StateMapper.build_decision_state` hands it the state.
- `Backtester.replay` walks stored bars once and answers two questions, so `run()`
  reports and `train()` fits the same walk; `RLTrainer.fit` is the TD(0) pass,
  `BACKTEST_TRAIN_EPOCHS` runs it, and `save_model_weights`/`load_value_function`
  move the parameters across processes.

## Phase 3b/4b — The hygiene batch (found while implementing Phases 3 and 4)

The bugs recorded while the earlier phases ran were unrelated to the task that
found them, so they were left open and are closed as one batch:

- `logs/` held 105 per-run files (390 MB) rotation could never remove; they are
  deleted, `.gitignore` covers `logs/kdr.log.*`, and the deploy excludes `logs/*`.
- The four entry scripts are consolidated onto `bin/_start_common.sh`: one PID file
  (`logs/kdr.pid`), one interpreter (`.venv/bin/python`), one parser, one
  pre-flight import.
- The `import gymnasium` probe in `bin/local-start.sh` is gone; the package it warns
  about is no longer a dependency.
- The web server's two layers take distinct logger names.
- The `--- Logging error ---` flake is fixed where it starts: the pipeline's unit
  tests keep the dashboard's uvicorn thread out of scope, so no thread outlives the
  test that started it.

## Phase 2c — The dashboard's honesty (found while implementing Phase 2b)

The live path now asks the model, which made three places where the dashboard
said more than the system knew:

- The confidence pushed with a sized order was a constant `0.75` next to a real
  decision; it is now the confidence `decide` reported for that decision, and an
  action translated without a report says that is all it has.
- The fallback recommendation is momentum, but its reasoning strings named Bellman
  thresholds and a negative expected return. The strings now name the band the
  momentum was compared against.
- Two processes training the same key both claimed an empty slot; the loser of that
  race used to get an `IntegrityError` out of the pass. It rolls back and stores
  against the record that won.

## Verification

Run after each phase, not just at the end:

```
python -m pytest tests/          # suite must stay green
python -m pytest tests/unit/test_database_integration.py   # real SQLAlchemy, no mocks
USE_QA_DB=1 python -m pytest tests/unit/test_database_integration.py  # postgres tier, self-skips if unreachable
python -m pytest tests/test_integration_pipeline.py        # domain/model/execution/stream contracts
mypy src/                        # committed config, no ad-hoc flags
ruff check src/ tests/           # catches the dead imports and broad excepts
psql -f schema.sql               # only if Phase 1 keeps the file
```

The whole `tests/` tree is collected and fast now: the two live-server modules
skip themselves when the configured database is unreachable, so `pytest tests/`
finishes in seconds rather than minutes. To run those modules against a real
server, start the app and point `DATABASE_URL` at a reachable host.

## Risk and ordering

Phases 0 and 1 are low risk and unblock verification. Phase 2 changes the
learning signal and is the highest-value change but also the one most likely to
change behavior — do it behind a config flag so the old vectorization stays
comparable. Phase 3 is mechanical. Phase 4 is refactoring; do it after the
behavior is correct so the diff stays reviewable.
