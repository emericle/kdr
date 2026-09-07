# 05: backtest-validation

**What to build:** A suite of backtests that validates the full end-to-end flow (Raw Data -> Model -> Decision) against historical performance benchmarks.

**Blocked by:** 03-integrate-rl-training-loop, 04-map-execution-data-pipeline

**Status:** ready-for-agent

- [ ] Develop a backtesting engine that feeds historical data into the pipeline.
- [ ] Implement validation metrics to compare model performance against baselines.
- [ ] Verify that the full loop from raw data to decision is consistent and accurate.
