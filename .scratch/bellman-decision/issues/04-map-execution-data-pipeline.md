# 04: map-execution-data-pipeline

**What to build:** Bridges the gap between raw Alpaca/Adanos API responses and the internal state/action schema, ensuring the real-world data is correctly ingested for the model.

**Blocked by:** 01-define-state-action-schema

**Status:** ready-for-agent

- [ ] Create adapters for Alpaca and Adanos data sources.
- [ ] Implement mapping logic to transform external data into the internal state schema.
- [ ] Verify that the data pipeline accurately populates the data buffer.
