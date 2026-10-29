"""End-to-end contract for the trading pipeline.

Each test wires one stage of the system the way the running application wires
it, then asserts on the values that stage hands to the next. The only mocks are
the two external seams: the Alpaca client and the database. Everything between
them, state mapping through order tracking, runs real.

These tests replace ``tests/integration_tests.py``, a script that swallowed
every exception and printed a tally, so a broken stage could report success.
"""

import os
import queue
import tempfile
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np

from src.domain import FullState, MarketState, PortfolioState, TradeMovement
from src.model import BellmanEngine
from src.reporting import ChartGenerator, CSVReporter, PerformanceAnalyzer
from src.scraper import AlpacaStreamProcessor, DBWriterWorker
from src.trading_execution import (
    AlpacaClient,
    OrderRecord,
    OrderTracker,
    PositionSizing,
    RiskManager,
    TradingExecutor,
)


def test_bellman_engine_discounts_next_state_value():
    """The Bellman target is the reward plus the discounted value of the next state."""
    engine = BellmanEngine(discount_factor=0.95)
    target_q = engine.compute_target_q(reward=20.0, next_state_max_q=15.0)
    assert target_q == 20.0 + 0.95 * 15.0


def test_full_state_composes_market_and_portfolio():
    """FullState wraps a market and a portfolio and flattens to a fixed-width vector."""
    market = MarketState(prices={"AAPL": 155.0, "MSFT": 250.0}, sentiment_scores={"AAPL": 0.75})
    portfolio = PortfolioState(cash=10000.0, holdings={"AAPL": 50.0})

    state = FullState(market=market, portfolio=portfolio)
    vector = state.to_vector(["AAPL", "MSFT"])

    # Per symbol: log price change (0 with no previous price), sentiment scaled
    # onto [-1, 1] (a missing score is the bottom of the scale), position flag.
    # Then cash as a fraction of the portfolio's total value.
    assert len(vector) == 7
    assert np.allclose(
        vector,
        [
            0.0,
            0.5,
            1.0,
            0.0,
            -1.0,
            0.0,
            10000.0 / (10000.0 + 50.0 * 155.0),
        ],
    )

    # The legacy layout is the raw one the old model trained on, and it is the
    # same length, so a run under either flag can be compared.
    legacy = state.to_vector(["AAPL", "MSFT"], schema="legacy")
    assert legacy.tolist() == [155.0, 50.0, 0.75, 250.0, 0.0, 0.0, 10000.0]


def test_risk_manager_caps_order_at_kelly_fraction():
    """Kelly sizing caps at 20% of the account, and the risk manager trims the order to fit."""
    kelly_size = PositionSizing.calculate_kelly(
        win_rate=0.6,
        win_loss_ratio=2.0,
        account_size=10000.0,
    )
    assert kelly_size == 2000.0

    risk_manager = RiskManager(max_position_pct=0.20)
    result = risk_manager.evaluate_order(
        symbol="AAPL",
        side="buy",
        shares=20,
        price=150.0,
        cash=10000.0,
        portfolio_value=10000.0,
        current_shares=0,
    )

    assert result["approved"] is True
    assert result["adjusted_shares"] == 13  # 13 * 150 = 1950 <= 2000


def test_order_tracker_nets_buys_against_sells():
    """Recording orders nets into a single position per symbol."""
    tracker = OrderTracker()
    tracker.record_order({"symbol": "AAPL", "side": "buy", "quantity": 10, "price": 150.0})
    tracker.record_order({"symbol": "AAPL", "side": "sell", "quantity": 5, "price": 155.0})

    assert len(tracker.get_order_history()) == 2
    assert tracker.get_positions()["AAPL"] == 5


def test_reporting_stage_writes_its_three_artifacts():
    """CSV, chart and performance metrics are all produced from the same trade list."""
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = os.path.join(tmpdir, "trading_report.csv")
        CSVReporter().generate_report(
            [
                {
                    "timestamp": datetime.now(),
                    "symbol": "AAPL",
                    "action": "buy",
                    "quantity": 10,
                    "price": 150.0,
                }
            ],
            csv_path,
        )
        assert os.path.exists(csv_path)

        chart_path = os.path.join(tmpdir, "chart.html")
        ChartGenerator().create_portfolio_chart(
            [
                {"timestamp": "2023-01-01", "value": 10000.0, "benchmark": 10000.0},
                {"timestamp": "2023-01-02", "value": 10500.0, "benchmark": 10100.0},
            ],
            chart_path,
        )
        assert os.path.exists(chart_path)

        metrics = PerformanceAnalyzer().calculate_metrics(
            [
                {"entry": 100, "exit": 105, "qty": 100},
                {"entry": 200, "exit": 220, "qty": 50},
            ]
        )
        assert metrics["total_return"] == 1500.0


def test_decision_executes_through_risk_sizing_to_a_tracked_order():
    """A BUY decision is sized, submitted and recorded as one filled order.

    The Alpaca client is the only mock: it stands in for the broker.
    """
    alpaca_client = MagicMock(spec=AlpacaClient)
    alpaca_client.submit_order.return_value = OrderRecord(
        id="e2e-1",
        symbol="AAPL",
        quantity=13,
        filled_qty=13,
        side="buy",
        order_type="market",
        status="filled",
        price=150.0,
    )

    executor = TradingExecutor(alpaca_client=alpaca_client)
    executed = executor.execute_decision(
        action=TradeMovement.BUY,
        symbol="AAPL",
        current_price=150.0,
        portfolio_cash=10000.0,
        portfolio_value=10000.0,
    )

    assert executed is not None
    assert executed.symbol == "AAPL"
    assert executed.filled_qty == 13
    assert len(executor.order_tracker.get_order_history()) == 1


def test_stream_bars_reach_the_database_writer():
    """A trade tick is buffered into a bar, queued, and persisted by the writer.

    The database is the mock; the stream processor and the writer run real.
    """
    db_manager = MagicMock()
    data_queue = queue.Queue()

    processor = AlpacaStreamProcessor(
        db_manager=db_manager,
        state_mapper=MagicMock(),
        symbols=["AAPL"],
        data_queue=data_queue,
    )
    processor.handle_trade(
        {
            "symbol": "AAPL",
            "price": 182.5,
            "size": 100,
            "timestamp": datetime(2023, 1, 1, 10, 0, 0),
        }
    )
    assert "AAPL" in processor.flush_ready_bars()

    worker = DBWriterWorker(db_manager, MagicMock(), data_queue)
    worker.start()
    data_queue.put(None)  # sentinel: the writer stops and the queue drains
    worker.join(timeout=2)

    assert db_manager.add_market_data.called
    written = db_manager.add_market_data.call_args[0][0]
    assert [bar["symbol"] for bar in written] == ["AAPL"], (
        f"the writer lost the queue's symbol: {written}"
    )
