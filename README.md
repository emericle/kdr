# kdr
First AI driven development project. Create a Bellmen decision model to simulate equities purchasing decisions.
# Financial Model - Bellman Decision & Multi-Asset Trading
2: 
3: A Python 3.14-powered trading engine that leverages a Bellman decision model to determine optimal actions (Buy, Sell, Hold) for equity assets based on market sentiment, volatility, and technical indicators.
4: 
5: ## Features
6: - **Decision Engine:** Built with Gymnasium and RL models.
7: - **Risk Management:** Dynamic position sizing using the Kelly Criterion with a 20% capital cap.
8: - **Data Integration:** Real-time tick ingestion from Alpaca and Sentiment analysis via Adanos.
9: - **Persistence:** Powered by PostgreSQL to store historical bars, sentiment scores, and persistent model weights across sessions.
10: 
11: ## Requirements
12: - Python 3.14+ (REQUIRED)
13: - PostgreSQL Database
14: - `alpaca-trade-api` (Alpaca API)
15: - Adanos (Sentiment Analysis)
16: 
17: ## Setup
18: 1. Ensure Python 3.14+ is installed on your system.
19: 2. Create the virtual environment: `python3.14 -m venv .venv`
20: 3. Activate the environment: `source .venv/bin/activate`
21: 4. Install dependencies: `pip install -r requirements.txt`
22: 5. Configure `.env` with the following variables:
23:    - `ALPACA_API_KEY`
24:    - `ALPACA_SECRET_KEY`
25:    - `ADANOS_API_KEY`
26:    - `DATABASE_URL` (or separate connection credentials)
27: 
28: ## Directory Structure
29: - `/src`: Core logic (TradingEnv, Data Ingestion, Model Weights)
30: - `/outputs`: Automated reports (CSV and Plotly charts) categorized by date.
31: - `/tests`: Unit and integration tests.
32: 
33: ## Execution
34: 
35: Run the system using:
36: ```bash
37:     python src/scraper.py --debug
38: ```
39: 
40: ### Available Flags
41: | Flag | Description |
42: | :--- | :--- |
43: | `--debug` | Enables verbose logging and debug output. |
44: 
45: Note: Running `python src/scraper.py --help` will display all available command-line options.
