# TODO List

## 1. Core Engine & Logic
- [x] **Implement `src/model.py`**: Define the Bellman decision model, Gymnasium environment, and RL training logic.
- [ ] **Complete `src/scraper.py`**: Implement the actual `AlpacaStreamProcessor` to connect to live websocket streams.
- [x] **Implement Risk Management**: Add logic for the Kelly Criterion and dynamic position sizing.

## 2. Data Ingestion Expansion
- [x] **Adanos Sentiment Integration**: Create a worker to fetch sentiment scores and store them using `SentimentDataModel`.
- [x] **Unified Data Pipeline**: Ensure sentiment data is correctly correlated with market price data for the model.

## 3. Trading Execution
- [x] **Alpaca Order Integration**: Implement functionality to translate model decisions (Buy/Sell/Hold) into actual API calls to Alpaca.

## 4. Observability & Reporting
- [x] **Automated Reporting Engine**: Develop logic to generate the CSV and Plotly charts described in the `README.md`.

## 5. Testing & Verification
- [x] **Expand Test Suite**: Add unit tests for the new model and sentiment modules.
- [x] **Integration Testing**: Verify the full pipeline from data ingestion to trading decision.
