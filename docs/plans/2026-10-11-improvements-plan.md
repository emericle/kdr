# Improvement Plan — KDR Code Review Findings (2026-10-11)

Derived from a line-by-line review of `src/` against `docs/architecture.md` and
`docs/specs/bellman_decision.md`. Every finding below was verified against the
code, not the docs. Companion checklist: [TODO](2026-10-11-todo.md).

## Phase 0 — Restore trust in the test suite

The suite currently passes against code that is broken in the field. `tests/unit/test_database.py:218`
and `:407` mock `filter_by`, a method SQLAlchemy sessions do not have, so the
real call path (`src/database.py:354`) never runs. Until the DB tests exercise
real SQLAlchemy, no DB fix can be verified.

Approach: keep mocks for behavior tests, but add integration tests that run
against the QA database the repo already supports (`SKIP_DB_TEST` / `USE_QA_DB`
in `src/database.py:25`). The QA DB URL is the mechanism already in place; the
gap is that no test uses it.

## Phase 1 — Correctness bugs

| Finding | Location | Fix |
|---|---|---|
| `filter_by` is not a SQLAlchemy method; `get_model_weights` raises `AttributeError` on every call | `src/database.py:354` | `.filter(ModelWeightsModel.symbol == symbol)` |
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
  that is exactly how the `filter_by` bug stayed invisible. Narrow to specific
  types; keep broad catches only at thread/process boundaries.
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

## Verification

Run after each phase, not just at the end:

```
python -m pytest tests/          # suite must stay green
mypy src/                        # committed config, no ad-hoc flags
ruff check src/ tests/           # catches the dead imports and broad excepts
psql -f schema.sql               # only if Phase 1 keeps the file
```

## Risk and ordering

Phases 0 and 1 are low risk and unblock verification. Phase 2 changes the
learning signal and is the highest-value change but also the one most likely to
change behavior — do it behind a config flag so the old vectorization stays
comparable. Phase 3 is mechanical. Phase 4 is refactoring; do it after the
behavior is correct so the diff stays reviewable.
