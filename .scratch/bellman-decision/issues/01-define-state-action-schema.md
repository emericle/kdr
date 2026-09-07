# 01: define-state-action-schema

**What to build:** Formalize the state space (market conditions, account status) and action space (buy, sell, hold) as schema-backed objects, ensuring all downstream logic consumes validated domain objects.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] Define a `State` dataclass/schema that includes market data and portfolio status.
- [x] Define an `Action` enum/schema for valid trade movements.
- [x] Implement validation logic to ensure incoming data maps correctly to these types.

**Status:** completed
