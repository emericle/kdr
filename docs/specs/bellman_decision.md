# Specification: Bellman Decision Model for Equity Portfolio Management

## Problem
The current portfolio management system needs a more robust decision-making framework to handle complex market conditions. Specifically, we need to move away from simpler heuristic logic toward a Bellman-based decision model to optimize the equity portfolio.

## Solution
Implement a Bellman Decision model for portfolio management. This involves:
1.  Defining clear state and action spaces for the portfolio.
2.  Implementing the core Bellman mathematical logic.
3.  Integrating an RL training loop.
4.  Mapping the model to real-world data from Alpaca and Adanos.
5.  Validating the implementation via backtesting.

## Requirements
- **Python Version**: Must run on Python 3.14+.
- **Data Sources**: Integrates with Alpaca for market data and Adanos for specific financial signals.
- **Database**: Persists training and results in PostgreSQL.

## User Stories
- As a portfolio manager, I want the system to calculate utility-based decisions based on the Bellman equation so that I can optimize for long-term growth.
- As a system operator, I want a robust data ingestion pipeline that feeds into the Bellman model automatically to ensure continuous operation.

## Out of Scope
- UI/Frontend for real-time trading.
- High-frequency trading (HFT) execution logic (this is a portfolio management model).
- Advanced multi-asset portfolio diversification (initially limited to equity).
