import pytest
from unittest.mock import MagicMock, patch
import os
import requests


class TestPositionSizing:
    """Unit tests for Kelly Criterion and dynamic position sizing."""

    def test_kelly_criterion_positive_edge(self):
        from src.trading_execution import PositionSizing

        # 60% win rate, 2:1 win/loss ratio, $10,000 account
        # Kelly fraction = (0.60 * 2.0 - 0.40) / 2.0 = 0.80 / 2.0 = 0.40
        # If capped at default max_fraction=0.20 (20% cap per spec), size = 10000 * 0.20 = 2000
        size_default = PositionSizing.calculate_kelly(
            win_rate=0.6,
            win_loss_ratio=2.0,
            account_size=10000.0
        )
        assert size_default == 2000.0

        # With explicit max_fraction=0.25, size = 10000 * 0.25 = 2500
        size_capped = PositionSizing.calculate_kelly(
            win_rate=0.6,
            win_loss_ratio=2.0,
            account_size=10000.0,
            max_fraction=0.25
        )
        assert size_capped == 2500.0

        size_uncapped = PositionSizing.calculate_kelly(
            win_rate=0.6,
            win_loss_ratio=2.0,
            account_size=10000.0,
            max_fraction=1.0
        )
        assert size_uncapped == 4000.0

    def test_kelly_criterion_zero_or_negative_edge(self):
        from src.trading_execution import PositionSizing

        # 30% win rate, 1:1 win/loss ratio -> negative edge -> 0 size
        size = PositionSizing.calculate_kelly(
            win_rate=0.3,
            win_loss_ratio=1.0,
            account_size=10000.0
        )
        assert size == 0.0

    def test_kelly_criterion_validation(self):
        from src.trading_execution import PositionSizing

        with pytest.raises(ValueError, match="Win rate"):
            PositionSizing.calculate_kelly(win_rate=1.5, win_loss_ratio=2.0, account_size=10000.0)

        with pytest.raises(ValueError, match="Account size"):
            PositionSizing.calculate_kelly(win_rate=0.5, win_loss_ratio=2.0, account_size=-1000.0)

    def test_calculate_shares(self):
        from src.trading_execution import PositionSizing

        # $2500 allocation at $155/share -> 16 shares ($2480)
        shares = PositionSizing.calculate_shares(
            target_dollar_amount=2500.0,
            share_price=155.0
        )
        assert shares == 16
        assert shares * 155.0 <= 2500.0

        # Price <= 0 returns 0
        assert PositionSizing.calculate_shares(2500.0, 0.0) == 0


class TestRiskManager:
    """Unit tests for RiskManager portfolio protection."""

    def test_evaluate_order_within_limits(self):
        from src.trading_execution import RiskManager

        rm = RiskManager(max_position_pct=0.20)
        # Account cash $10,000, max position = $2,000
        # Buy 10 shares at $150 = $1,500 <= $2,000
        result = rm.evaluate_order(
            symbol="AAPL",
            side="buy",
            shares=10,
            price=150.0,
            cash=10000.0,
            portfolio_value=10000.0,
            current_shares=0
        )
        assert result["approved"] is True
        assert result["adjusted_shares"] == 10

    def test_evaluate_order_exceeds_max_position_cap(self):
        from src.trading_execution import RiskManager

        rm = RiskManager(max_position_pct=0.20)
        # Account cash $10,000, max position = $2,000
        # Buy 20 shares at $150 = $3,000 > $2,000
        # Should adjust down to 13 shares ($1,950 <= $2,000)
        result = rm.evaluate_order(
            symbol="AAPL",
            side="buy",
            shares=20,
            price=150.0,
            cash=10000.0,
            portfolio_value=10000.0,
            current_shares=0
        )
        assert result["approved"] is True
        assert result["adjusted_shares"] == 13
        assert result["adjusted_shares"] * 150.0 <= 2000.0

    def test_evaluate_order_insufficient_cash(self):
        from src.trading_execution import RiskManager

        rm = RiskManager(max_position_pct=0.50)
        # Portfolio value $10,000, but cash only $500
        result = rm.evaluate_order(
            symbol="AAPL",
            side="buy",
            shares=10,
            price=150.0,
            cash=500.0,
            portfolio_value=10000.0,
            current_shares=0
        )
        assert result["approved"] is True
        assert result["adjusted_shares"] == 3  # 3 * 150 = 450 <= 500

    def test_evaluate_sell_order_cannot_exceed_holdings(self):
        from src.trading_execution import RiskManager

        rm = RiskManager()
        # Currently hold 10 shares, cannot sell 20
        result = rm.evaluate_order(
            symbol="AAPL",
            side="sell",
            shares=20,
            price=150.0,
            cash=5000.0,
            portfolio_value=6500.0,
            current_shares=10
        )
        assert result["approved"] is True
        assert result["adjusted_shares"] == 10


class TestOrderValidator:
    """Unit tests for OrderValidator."""

    def test_valid_order(self):
        from src.trading_execution import OrderValidator

        validator = OrderValidator()
        order = {
            "symbol": "AAPL",
            "quantity": 100,
            "side": "buy",
            "order_type": "market"
        }
        assert validator.validate_order(order) is True

    def test_invalid_order_negative_quantity(self):
        from src.trading_execution import OrderValidator

        validator = OrderValidator()
        order = {
            "symbol": "AAPL",
            "quantity": -5,
            "side": "buy",
            "order_type": "market"
        }
        assert validator.validate_order(order) is False

    def test_invalid_order_missing_fields(self):
        from src.trading_execution import OrderValidator

        validator = OrderValidator()
        assert validator.validate_order({}) is False
        assert validator.validate_order({"symbol": "AAPL"}) is False

    def test_invalid_order_unknown_side(self):
        from src.trading_execution import OrderValidator

        validator = OrderValidator()
        order = {
            "symbol": "AAPL",
            "quantity": 10,
            "side": "invalid_side",
            "order_type": "market"
        }
        assert validator.validate_order(order) is False


class TestOrderTracker:
    """Unit tests for OrderTracker."""

    def test_record_and_retrieve_history(self):
        from src.trading_execution import OrderTracker

        tracker = OrderTracker()
        order1 = {
            "symbol": "AAPL",
            "quantity": 100,
            "side": "buy",
            "price": 150.0,
            "status": "filled"
        }
        order2 = {
            "symbol": "MSFT",
            "quantity": 50,
            "side": "sell",
            "price": 300.0,
            "status": "filled"
        }
        tracker.record_order(order1)
        tracker.record_order(order2)

        history = tracker.get_order_history()
        assert len(history) == 2
        assert history[0]["symbol"] == "AAPL"
        assert history[1]["symbol"] == "MSFT"

    def test_filter_history_by_symbol(self):
        from src.trading_execution import OrderTracker

        tracker = OrderTracker()
        tracker.record_order({"symbol": "AAPL", "quantity": 10, "side": "buy"})
        tracker.record_order({"symbol": "GOOGL", "quantity": 5, "side": "buy"})
        tracker.record_order({"symbol": "AAPL", "quantity": 10, "side": "sell"})

        aapl_history = tracker.get_order_history("AAPL")
        assert len(aapl_history) == 2
        assert all(o["symbol"] == "AAPL" for o in aapl_history)

    def test_net_positions(self):
        from src.trading_execution import OrderTracker

        tracker = OrderTracker()
        tracker.record_order({"symbol": "AAPL", "quantity": 100, "side": "buy", "status": "filled"})
        tracker.record_order({"symbol": "AAPL", "quantity": 40, "side": "sell", "status": "filled"})
        tracker.record_order({"symbol": "GOOGL", "quantity": 20, "side": "buy", "status": "filled"})

        positions = tracker.get_positions()
        assert positions["AAPL"] == 60
        assert positions["GOOGL"] == 20


class TestAlpacaClient:
    """Unit tests for AlpacaClient."""

    def test_client_init(self):
        from src.trading_execution import AlpacaClient

        client = AlpacaClient(api_key="test_key", api_secret="test_secret")
        assert client.api_key == "test_key"
        assert client.api_secret == "test_secret"

    @patch.object(requests.Session, "post")
    def test_submit_order_success(self, mock_post):
        from src.trading_execution import AlpacaClient

        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            "id": "order-123",
            "symbol": "AAPL",
            "qty": "100",
            "filled_qty": "100",
            "side": "buy",
            "type": "market",
            "status": "filled"
        }

        client = AlpacaClient(api_key="test_key", api_secret="test_secret")
        order = client.submit_order(
            symbol="AAPL",
            quantity=100,
            side="buy",
            order_type="market"
        )
        assert order is not None
        assert order.symbol == "AAPL"
        assert order.side == "buy"
        assert order.filled_qty == 100
        assert order.status == "filled"

    @patch.object(requests.Session, "get")
    def test_get_account(self, mock_get):
        from src.trading_execution import AlpacaClient

        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = {
            "id": "acc-123",
            "cash": "10000.00",
            "portfolio_value": "15000.00"
        }

        client = AlpacaClient(api_key="test_key", api_secret="test_secret")
        account = client.get_account()
        assert float(account["cash"]) == 10000.0
        assert float(account["portfolio_value"]) == 15000.0


class TestTradingExecutor:
    """Unit tests for TradingExecutor orchestration."""

    def test_translate_decision_buy(self):
        from src.trading_execution import TradingExecutor, AlpacaClient, RiskManager
        from src.domain import TradeMovement, Action

        client = AlpacaClient(api_key="key", api_secret="sec")
        executor = TradingExecutor(alpaca_client=client)

        order = executor.translate_decision(
            action=TradeMovement.BUY,
            symbol="AAPL",
            current_price=100.0,
            portfolio_cash=5000.0,
            portfolio_value=10000.0,
            win_rate=0.6,
            win_loss_ratio=2.0
        )
        assert order is not None
        assert order["symbol"] == "AAPL"
        assert order["side"] == "buy"
        assert order["quantity"] > 0
        # Check cash cap
        assert order["quantity"] * 100.0 <= 5000.0

    def test_translate_decision_hold_returns_none(self):
        from src.trading_execution import TradingExecutor, AlpacaClient
        from src.domain import TradeMovement

        client = AlpacaClient(api_key="key", api_secret="sec")
        executor = TradingExecutor(alpaca_client=client)

        order = executor.translate_decision(
            action=TradeMovement.HOLD,
            symbol="AAPL",
            current_price=100.0,
            portfolio_cash=5000.0
        )
        assert order is None

    def test_translate_decision_sell(self):
        from src.trading_execution import TradingExecutor, AlpacaClient
        from src.domain import TradeMovement

        client = AlpacaClient(api_key="key", api_secret="sec")
        executor = TradingExecutor(alpaca_client=client)

        order = executor.translate_decision(
            action=TradeMovement.SELL,
            symbol="AAPL",
            current_price=100.0,
            portfolio_cash=5000.0,
            current_shares=15
        )
        assert order is not None
        assert order["symbol"] == "AAPL"
        assert order["side"] == "sell"
        assert order["quantity"] == 15

    def test_execute_decision_end_to_end(self):
        from src.trading_execution import TradingExecutor, AlpacaClient, OrderRecord
        from src.domain import TradeMovement

        mock_client = MagicMock(spec=AlpacaClient)
        mock_client.submit_order.return_value = OrderRecord(
            id="ord-999",
            symbol="AAPL",
            quantity=10,
            filled_qty=10,
            side="buy",
            order_type="market",
            status="filled",
            price=150.0
        )

        executor = TradingExecutor(alpaca_client=mock_client)
        executed = executor.execute_decision(
            action=TradeMovement.BUY,
            symbol="AAPL",
            current_price=150.0,
            portfolio_cash=5000.0,
            portfolio_value=10000.0
        )

        assert executed is not None
        assert executed.id == "ord-999"
        assert executed.symbol == "AAPL"
        assert len(executor.order_tracker.get_order_history()) == 1

    def test_execute_decision_and_order_tracker_status_filtering(self):
        from src.trading_execution import TradingExecutor, AlpacaClient, OrderRecord, OrderTracker
        from src.domain import TradeMovement

        mock_client = MagicMock(spec=AlpacaClient)
        mock_client.submit_order.return_value = OrderRecord(
            id="ord-1000",
            symbol="TSLA",
            quantity=5,
            filled_qty=5,
            side="buy",
            order_type="market",
            status="filled",
            price=200.0
        )

        tracker = OrderTracker(max_history=2)
        executor = TradingExecutor(alpaca_client=mock_client, order_tracker=tracker)
        executed = executor.execute_decision(
            action=TradeMovement.BUY,
            symbol="TSLA",
            current_price=200.0,
            portfolio_cash=5000.0,
            portfolio_value=10000.0
        )
        assert executed is not None
        assert tracker.get_position("TSLA") == 5.0

        # Disregard rejected order
        tracker.record_order({
            "symbol": "TSLA",
            "quantity": 10,
            "side": "buy",
            "status": "rejected"
        })
        assert tracker.get_position("TSLA") == 5.0

        # Test clear
        tracker.clear()
        assert tracker.get_position("TSLA") == 0.0
        assert tracker.get_positions() == {}

    def test_alpaca_client_session_pooling(self):
        import requests
        from src.trading_execution import AlpacaClient

        mock_session = MagicMock(spec=requests.Session)
        mock_session.headers = {}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "acc-pool"}
        mock_session.get.return_value = mock_resp

        client = AlpacaClient(api_key="k", api_secret="s", session=mock_session)
        acc = client.get_account()
        assert acc["id"] == "acc-pool"
        mock_session.get.assert_called_once()

    def test_kelly_zero_account_or_ratio(self):
        from src.trading_execution import PositionSizing
        assert PositionSizing.calculate_kelly(0.6, -1.0, 1000.0) == 0.0
        assert PositionSizing.calculate_kelly(0.6, 2.0, 0.0) == 0.0

    def test_risk_manager_edge_cases(self):
        from src.trading_execution import RiskManager
        rm = RiskManager()
        # Shares <= 0
        res = rm.evaluate_order("AAPL", "buy", 0, 150.0, 1000.0, 10000.0)
        assert not res["approved"]
        # Buy with 0 cash available
        res_cash = rm.evaluate_order("AAPL", "buy", 10, 150.0, 0.0, 10000.0)
        assert not res_cash["approved"]
        # Sell with 0 current shares
        res_sell = rm.evaluate_order("AAPL", "sell", 10, 150.0, 1000.0, 10000.0, current_shares=0)
        assert not res_sell["approved"]
        # Unsupported side
        res_other = rm.evaluate_order("AAPL", "unknown", 10, 150.0, 1000.0, 10000.0)
        assert not res_other["approved"]

    def test_order_validator_edge_cases(self):
        from src.trading_execution import OrderValidator
        assert not OrderValidator.validate_order("not-a-dict")
        assert not OrderValidator.validate_order({"symbol": "", "quantity": 10, "side": "buy"})
        assert not OrderValidator.validate_order({"symbol": "AAPL", "quantity": -5, "side": "buy"})
        assert not OrderValidator.validate_order({"symbol": "AAPL", "quantity": 10, "side": "invalid_side"})
        assert OrderValidator.validate_order({"symbol": "AAPL", "quantity": 10, "side": "buy"})

    def test_order_tracker_invalid(self):
        from src.trading_execution import OrderTracker
        tracker = OrderTracker(max_history=1)
        tracker.record_order({})
        tracker.record_order({})
        assert len(tracker.get_order_history()) == 1

    def test_alpaca_client_errors_and_limit_price(self):
        from src.trading_execution import AlpacaClient
        mock_session = MagicMock()
        client = AlpacaClient(api_key="k", api_secret="s", session=mock_session)

        # submit_order with limit_price
        mock_resp_limit = MagicMock()
        mock_resp_limit.status_code = 200
        mock_resp_limit.json.return_value = {
            "id": "ord-1", "symbol": "AAPL", "qty": "10", "filled_qty": "10",
            "side": "buy", "type": "limit", "status": "filled", "filled_avg_price": "150.5"
        }
        mock_session.post.return_value = mock_resp_limit
        order = client.submit_order("AAPL", 10, "buy", "limit", limit_price=150.0)
        assert order.price == 150.5

        # submit_order error status
        mock_resp_err = MagicMock()
        mock_resp_err.status_code = 400
        mock_resp_err.text = "Insufficient funds"
        mock_session.post.return_value = mock_resp_err
        err_order = client.submit_order("AAPL", 10, "buy")
        assert "rejected" in err_order.status

        # submit_order exception
        mock_session.post.side_effect = Exception("Network timeout")
        except_order = client.submit_order("AAPL", 10, "buy")
        assert "error" in except_order.status
        mock_session.post.side_effect = None

        # get_account error and exception
        mock_resp_acc_err = MagicMock()
        mock_resp_acc_err.status_code = 500
        mock_session.get.return_value = mock_resp_acc_err
        assert client.get_account() == {}
        mock_session.get.side_effect = Exception("Acc error")
        assert client.get_account() == {}
        mock_session.get.side_effect = None

        # get_positions success, error and exception
        mock_resp_pos = MagicMock()
        mock_resp_pos.status_code = 200
        mock_resp_pos.json.return_value = [{"symbol": "AAPL", "qty": "10"}]
        mock_session.get.return_value = mock_resp_pos
        assert len(client.get_positions()) == 1

        mock_resp_pos_err = MagicMock()
        mock_resp_pos_err.status_code = 500
        mock_session.get.return_value = mock_resp_pos_err
        assert client.get_positions() == []

        mock_session.get.side_effect = Exception("Pos error")
        assert client.get_positions() == []

    def test_executor_decision_hold_and_unsupported(self):
        from src.trading_execution import TradingExecutor
        from src.domain import TradeMovement
        executor = TradingExecutor(alpaca_client=MagicMock())
        assert executor.translate_decision(TradeMovement.HOLD, "AAPL", 150.0, 1000.0) is None
        assert executor.translate_decision("UNKNOWN_ACTION", "AAPL", 150.0, 1000.0) is None

    def test_executor_buy_sell_rejected_by_risk(self):
        from src.trading_execution import TradingExecutor, RiskManager
        from src.domain import TradeMovement
        mock_rm = MagicMock(spec=RiskManager)
        mock_rm.evaluate_order.return_value = {"approved": False, "adjusted_shares": 0, "reason": "Denied"}
        executor = TradingExecutor(alpaca_client=MagicMock(), risk_manager=mock_rm)

        buy_res = executor.translate_decision(TradeMovement.BUY, "AAPL", 150.0, 1000.0)
        assert buy_res is None

        sell_res = executor.translate_decision(TradeMovement.SELL, "AAPL", 150.0, 1000.0, current_shares=10)
        assert sell_res is None

    def test_executor_execute_decision_validation_failure(self):
        from src.trading_execution import TradingExecutor, OrderValidator
        from src.domain import TradeMovement
        mock_val = MagicMock(spec=OrderValidator)
        mock_val.validate_order.return_value = False
        executor = TradingExecutor(alpaca_client=MagicMock(), order_validator=mock_val)

        res = executor.execute_decision(
            action=TradeMovement.BUY,
            symbol="AAPL",
            current_price=150.0,
            portfolio_cash=5000.0,
            portfolio_value=10000.0
        )
        assert res is None


