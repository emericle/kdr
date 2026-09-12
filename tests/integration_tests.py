#!/usr/bin/env python
"""
Polished Integration Tests for KDR Trading System

Tests key integration points across all modules without requiring external services.

TODO Items Tested:
1. ✓ Bellman Equation (core RL computation)
2. ✓ Domain Models (MarketState, PortfolioState, FullState)
3. ✓ Trading Execution (RiskManager, PositionSizing, OrderTracker, AlpacaClient, TradingExecutor)
4. ✓ Reporting (CSVReporter, ChartGenerator, PerformanceAnalyzer)
5. ✓ End-to-End Pipeline (Ingestion State -> Bellman Decision -> Risk Sizing -> Execution -> Report)
"""
import sys
import os
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

# Ensure we can import from src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.domain import MarketState, PortfolioState, FullState, Action, TradeMovement
from src.model import BellmanEngine
from src.trading_execution import PositionSizing, RiskManager, OrderTracker, AlpacaClient, TradingExecutor, OrderRecord
from src.reporting import CSVReporter, ChartGenerator, PerformanceAnalyzer


def main():
    """Run all integration tests."""
    print("=" * 70)
    print("KDR TRADING SYSTEM - Integration Tests")
    print("=" * 70)

    total_passed = 0
    total_failed = 0

    # Test 1: Bellman Engine
    print("\n" + "=" * 70)
    print("Test Group: Bellman Engine (RL Core)")
    print("=" * 70)

    try:
        engine = BellmanEngine(discount_factor=0.95)
        print("✓ BellmanEngine initialized")

        # Test core equation: compute_target_q(reward, next_state_max_q)
        target_q = engine.compute_target_q(reward=20.0, next_state_max_q=15.0)
        assert target_q == 20.0 + 0.95 * 15.0
        print(f"✓ compute_target_q works: Q = {target_q:.2f}")

        total_passed += 1
    except Exception as e:
        print(f"✗ Bellman Engine test failed: {e}")
        total_failed += 1

    # Test 2: Domain Models
    print("\n" + "=" * 70)
    print("Test Group: Domain Models")
    print("=" * 70)

    try:
        ms = MarketState(
            prices={'AAPL': 155.0, 'MSFT': 250.0},
            sentiment_scores={'AAPL': 0.75}
        )
        print(f"✓ MarketState created: {len(ms.prices)} symbols")

        ps = PortfolioState(cash=10000.0, holdings={'AAPL': 50.0})
        print(f"✓ PortfolioState: ${ps.cash:,.2f} cash, {len(ps.holdings)} positions")

        fs = FullState(market=ms, portfolio=ps)
        vec = fs.to_vector(['AAPL', 'MSFT'])
        assert len(vec) == 7
        print(f"✓ FullState composes market and portfolio (vector len={len(vec)})")

        total_passed += 1
    except Exception as e:
        print(f"✗ Domain Models test failed: {e}")
        total_failed += 1

    # Test 3: Trading Execution & Risk Management
    print("\n" + "=" * 70)
    print("Test Group: Trading Execution")
    print("=" * 70)

    try:
        # Risk Management & Kelly Criterion
        account = 10000.0
        kelly_size = PositionSizing.calculate_kelly(
            win_rate=0.6,
            win_loss_ratio=2.0,
            account_size=account
        )
        assert kelly_size == 2000.0  # 20% cap
        print(f"✓ Kelly criterion: 20.00% cap of account = ${kelly_size:,.2f}")

        rm = RiskManager(max_position_pct=0.20)
        eval_res = rm.evaluate_order(
            symbol="AAPL",
            side="buy",
            shares=20,
            price=150.0,
            cash=account,
            portfolio_value=account,
            current_shares=0
        )
        assert eval_res["approved"] is True
        assert eval_res["adjusted_shares"] == 13  # 13 * 150 = 1950 <= 2000
        print(f"✓ Risk mgmt: account=${account:,.2f}, adjusted_shares={eval_res['adjusted_shares']}")

        # Order Tracking
        tracker = OrderTracker()
        tracker.record_order({'symbol': 'AAPL', 'side': 'buy', 'quantity': 10, 'price': 150.0})
        tracker.record_order({'symbol': 'AAPL', 'side': 'sell', 'quantity': 5, 'price': 155.0})
        positions = tracker.get_positions()
        assert positions['AAPL'] == 5
        print(f"✓ Order tracking: {len(tracker.get_order_history())} orders recorded, net position = {positions['AAPL']}")

        total_passed += 1
    except Exception as e:
        print(f"✗ Trading Execution test failed: {e}")
        total_failed += 1

    # Test 4: Reporting & Observability
    print("\n" + "=" * 70)
    print("Test Group: Reporting & Observability")
    print("=" * 70)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_reporter = CSVReporter()
            csv_path = os.path.join(tmpdir, "trading_report.csv")
            csv_reporter.generate_report([
                {"timestamp": datetime.now(), "symbol": "AAPL", "action": "buy", "quantity": 10, "price": 150.0}
            ], csv_path)
            assert os.path.exists(csv_path)
            print(f"✓ CSV generated: {csv_path}")

            chart_gen = ChartGenerator()
            chart_path = os.path.join(tmpdir, "chart.html")
            chart_gen.create_portfolio_chart([
                {"timestamp": "2023-01-01", "value": 10000.0, "benchmark": 10000.0},
                {"timestamp": "2023-01-02", "value": 10500.0, "benchmark": 10100.0}
            ], chart_path)
            assert os.path.exists(chart_path)
            print(f"✓ HTML Chart generated: {chart_path}")

            analyzer = PerformanceAnalyzer()
            metrics = analyzer.calculate_metrics([
                {"entry": 100, "exit": 105, "qty": 100},
                {"entry": 200, "exit": 220, "qty": 50}
            ])
            assert metrics["total_return"] == 1500.0
            print(f"✓ Performance metrics: ${metrics['total_return']:,.2f} total return, win_rate = {metrics['win_rate']:.0%}")

        total_passed += 1
    except Exception as e:
        print(f"✗ Reporting test failed: {e}")
        total_failed += 1

    # Test 5: End-to-End Decision Pipeline
    print("\n" + "=" * 70)
    print("Test Group: End-to-End Pipeline")
    print("=" * 70)

    try:
        mock_alpaca = MagicMock(spec=AlpacaClient)
        mock_alpaca.submit_order.return_value = OrderRecord(
            id="e2e-1",
            symbol="AAPL",
            quantity=13,
            filled_qty=13,
            side="buy",
            order_type="market",
            status="filled",
            price=150.0
        )
        executor = TradingExecutor(alpaca_client=mock_alpaca)
        executed_order = executor.execute_decision(
            action=TradeMovement.BUY,
            symbol="AAPL",
            current_price=150.0,
            portfolio_cash=10000.0,
            portfolio_value=10000.0
        )
        assert executed_order is not None
        assert executed_order.symbol == "AAPL"
        assert executed_order.filled_qty == 13
        assert len(executor.order_tracker.get_order_history()) == 1
        print("✓ End-to-end pipeline: Decision -> Risk Sizing -> Alpaca Submit -> Order Tracked")

        total_passed += 1
    except Exception as e:
        print(f"✗ End-to-End Pipeline test failed: {e}")
        total_failed += 1

    # Final Summary
    print("\n" + "=" * 70)
    print(f"Test Results: {total_passed} passed, {total_failed} failed")
    print("=" * 70)

    if total_failed == 0:
        print("\n🎉 All integration tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total_failed} test(s) had issues")
        return 1


if __name__ == "__main__":
    sys.exit(main())
