# Handoff Document

## Context Summary
The current project involves the development of a **Bellman Decision model** for equity portfolio management within a trading system. The system utilizes Python 3.14+, PostgreSQL, Alpaca, and Adanos, following a structured development workflow using skill-based automation.

## Progress to Date
- **Specifications**: A comprehensive specification has been prepared and saved to `docs/specs/bellman_decision.md`. It details the transition from current state to a robust Bellman-based decision logic.
- **Roadmap**: The project has been partitioned into 5 tracer-bullet tickets located in `.scratch/bellman_decision/issues/`.
    1. **01-define-state-action-schema**: Define core data structures.
    2. **02-implement-bellman-core**: Core mathematical logic implementation.
    3. **03-integrate-rl-training-loop**: RL loop integration.
    4. **04-map-execution-data-pipeline**: Real-world data mapping and validation.
    5. **05-backtest-validation**: Backtest and validation.
- **Infrastructure**: The environment is configured for Python 3.14, and the data pipeline (Alpaca -> Buffer -> DBWriter -> PostgreSQL) is established.

## Status
The planning and scoping phases are complete. The implementation phase begins with the first ticket in the queue: `01-define-state-action-schema`.

## Suggested Skills for Next Agent
- `implement`: To begin executing the tickets starting with `01-define-state-action-schema`.
- `diagnosing-bugs`: To be used if issues arise during the integration of the Bellman math or the data pipeline.
- `codebase-design`: For refining module interfaces during the implementation of the core logic.
- `research`: To look up specific Alpaca/Adanos API details if necessary during data mapping.

## Files/References
- Specification: `docs/specs/bellman_decision.md`
- Tickets: ` .scratch/bellman-decision/issues/`
- Configuration: `src/config_gatekeeper.py` and `.env`
