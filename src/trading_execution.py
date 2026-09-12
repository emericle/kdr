import os
import logging
import datetime
from dataclasses import dataclass
from typing import Dict, Any, Optional, List
import requests

logger = logging.getLogger("TradingExecution")


class PositionSizing:
    """
    Calculates position sizes based on risk management principles,
    including the Kelly Criterion and maximum portfolio allocation limits.
    """

    @staticmethod
    def calculate_kelly(
        win_rate: float,
        win_loss_ratio: float,
        account_size: float,
        max_fraction: float = 0.20
    ) -> float:
        """
        Calculates position size using the Kelly Criterion:
        f* = (p * b - (1 - p)) / b = p - (1 - p) / b

        Args:
            win_rate: Probability of winning trade (0.0 to 1.0)
            win_loss_ratio: Ratio of average win to average loss (> 0)
            account_size: Total capital / account equity (>= 0)
            max_fraction: Maximum allowed allocation fraction of the account (default 0.20)

        Returns:
            Calculated position size in currency units (e.g. USD).
        """
        if not (0.0 <= win_rate <= 1.0):
            raise ValueError("Win rate must be between 0.0 and 1.0")
        if account_size < 0.0:
            raise ValueError("Account size cannot be negative")
        if win_loss_ratio <= 0.0 or account_size == 0.0:
            return 0.0

        kelly_f = (win_rate * win_loss_ratio - (1.0 - win_rate)) / win_loss_ratio
        if kelly_f <= 0.0:
            return 0.0

        fraction = min(kelly_f, max_fraction)
        return round(account_size * fraction, 2)

    @staticmethod
    def calculate_shares(target_dollar_amount: float, share_price: float) -> int:
        """
        Calculates whole number of shares for a target dollar amount.
        """
        if share_price <= 0.0 or target_dollar_amount <= 0.0:
            return 0
        return int(target_dollar_amount // share_price)


class RiskManager:
    """
    Evaluates proposed orders against portfolio risk boundaries:
    - Maximum position size relative to portfolio value
    - Cash availability
    - Holding constraints on sells
    """

    def __init__(
        self,
        max_position_pct: float = 0.20,
        stop_loss_pct: float = 0.05
    ):
        self.max_position_pct = max_position_pct
        self.stop_loss_pct = stop_loss_pct

    def evaluate_order(
        self,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        cash: float,
        portfolio_value: float,
        current_shares: int = 0
    ) -> Dict[str, Any]:
        """
        Validates and adjusts an order based on risk limits.

        Returns:
            Dict with 'approved', 'adjusted_shares', and 'reason'.
        """
        normalized_side = side.lower()
        if shares <= 0:
            return {
                "approved": False,
                "adjusted_shares": 0,
                "reason": "Shares must be greater than zero"
            }

        if normalized_side == "buy":
            max_allowed_value = portfolio_value * self.max_position_pct
            current_position_value = current_shares * price
            remaining_position_capacity = max(0.0, max_allowed_value - current_position_value)

            # Cap by cash available and position cap
            max_capital_for_order = min(cash, remaining_position_capacity)
            max_shares_allowed = int(max_capital_for_order // price) if price > 0 else 0

            adjusted_shares = min(shares, max_shares_allowed)
            if adjusted_shares <= 0:
                return {
                    "approved": False,
                    "adjusted_shares": 0,
                    "reason": "Order exceeds position limit or available cash"
                }

            return {
                "approved": True,
                "adjusted_shares": adjusted_shares,
                "reason": "Order approved" if adjusted_shares == shares else "Shares adjusted down to comply with risk limits"
            }

        elif normalized_side == "sell":
            if current_shares <= 0:
                return {
                    "approved": False,
                    "adjusted_shares": 0,
                    "reason": f"No current position in {symbol} to sell"
                }

            adjusted_shares = min(shares, current_shares)
            return {
                "approved": True,
                "adjusted_shares": adjusted_shares,
                "reason": "Order approved" if adjusted_shares == shares else "Shares adjusted down to existing holdings"
            }

        else:
            return {
                "approved": False,
                "adjusted_shares": 0,
                "reason": f"Unsupported order side: {side}"
            }


class OrderValidator:
    """Validates trade orders before routing to execution."""

    @staticmethod
    def validate_order(order: Dict[str, Any]) -> bool:
        if not isinstance(order, dict):
            return False

        symbol = order.get("symbol")
        if not symbol or not isinstance(symbol, str) or not symbol.strip():
            return False

        quantity = order.get("quantity", order.get("qty", order.get("shares")))
        if quantity is None or not isinstance(quantity, (int, float)) or quantity <= 0:
            return False

        side = order.get("side")
        if not side or not isinstance(side, str):
            return False
        if side.lower() not in ("buy", "sell"):
            return False

        return True


class OrderTracker:
    """Maintains in-memory log of executed orders and tracks net positions."""

    def __init__(self):
        self._history: List[Dict[str, Any]] = []

    def record_order(self, order: Dict[str, Any]) -> Dict[str, Any]:
        record = dict(order)
        if "timestamp" not in record:
            record["timestamp"] = datetime.datetime.now(datetime.timezone.utc)
        self._history.append(record)
        return record

    def get_order_history(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        if symbol is None:
            return list(self._history)
        return [o for o in self._history if o.get("symbol") == symbol]

    def get_positions(self) -> Dict[str, float]:
        positions: Dict[str, float] = {}
        for order in self._history:
            sym = order.get("symbol")
            if not sym:
                continue
            qty = float(order.get("quantity", order.get("qty", 0)))
            side = str(order.get("side", "")).lower()
            if side == "buy":
                positions[sym] = positions.get(sym, 0.0) + qty
            elif side == "sell":
                positions[sym] = positions.get(sym, 0.0) - qty
        return positions

    def clear(self) -> None:
        self._history.clear()


@dataclass
class OrderRecord:
    """Represents an order response from Alpaca."""
    id: str
    symbol: str
    quantity: float
    filled_qty: float
    side: str
    order_type: str
    status: str
    price: Optional[float] = None


class AlpacaClient:
    """
    Client for placing orders and querying account data via Alpaca REST API.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        self.api_key = api_key or os.getenv("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("ALPACA_SECRET_KEY", "")
        self.base_url = (base_url or os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json"
        }

    def submit_order(
        self,
        symbol: str,
        quantity: float,
        side: str,
        order_type: str = "market",
        time_in_force: str = "day",
        limit_price: Optional[float] = None
    ) -> OrderRecord:
        url = f"{self.base_url}/orders"
        payload: Dict[str, Any] = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": time_in_force
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=10)
            if resp.status_code in (200, 201):
                data = resp.json()
                return OrderRecord(
                    id=str(data.get("id", "")),
                    symbol=data.get("symbol", symbol),
                    quantity=float(data.get("qty", quantity)),
                    filled_qty=float(data.get("filled_qty", quantity)),
                    side=data.get("side", side.lower()),
                    order_type=data.get("type", order_type),
                    status=data.get("status", "submitted"),
                    price=float(data["filled_avg_price"]) if data.get("filled_avg_price") else limit_price
                )
            else:
                logger.error(f"Alpaca submit_order failed [{resp.status_code}]: {resp.text}")
                return OrderRecord(
                    id="",
                    symbol=symbol,
                    quantity=float(quantity),
                    filled_qty=0.0,
                    side=side.lower(),
                    order_type=order_type,
                    status=f"rejected: {resp.status_code}"
                )
        except Exception as e:
            logger.error(f"Error submitting order to Alpaca: {e}")
            return OrderRecord(
                id="",
                symbol=symbol,
                quantity=float(quantity),
                filled_qty=0.0,
                side=side.lower(),
                order_type=order_type,
                status=f"error: {str(e)}"
            )

    def get_account(self) -> Dict[str, Any]:
        url = f"{self.base_url}/account"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except Exception as e:
            logger.error(f"Error getting account from Alpaca: {e}")
            return {}

    def get_positions(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/positions"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return []
        except Exception as e:
            logger.error(f"Error getting positions from Alpaca: {e}")
            return []


class TradingExecutor:
    """
    Translates model decisions into risk-managed orders and executes them with Alpaca.
    """

    def __init__(
        self,
        alpaca_client: AlpacaClient,
        risk_manager: Optional[RiskManager] = None,
        order_tracker: Optional[OrderTracker] = None,
        order_validator: Optional[OrderValidator] = None
    ):
        self.alpaca_client = alpaca_client
        self.risk_manager = risk_manager or RiskManager()
        self.order_tracker = order_tracker or OrderTracker()
        self.order_validator = order_validator or OrderValidator()

    def translate_decision(
        self,
        action: Any,
        symbol: str,
        current_price: float,
        portfolio_cash: float,
        current_shares: int = 0,
        portfolio_value: Optional[float] = None,
        win_rate: float = 0.6,
        win_loss_ratio: float = 2.0
    ) -> Optional[Dict[str, Any]]:
        # Extract string representation of trade movement
        movement = None
        if hasattr(action, "movement") and action.movement is not None:
            movement = str(action.movement.value if hasattr(action.movement, "value") else action.movement).upper()
        elif hasattr(action, "value"):
            movement = str(action.value).upper()
        elif isinstance(action, str):
            movement = action.upper()

        if movement in ("HOLD", "NONE", None):
            return None

        total_value = portfolio_value if portfolio_value is not None else portfolio_cash + (current_shares * current_price)

        if movement == "BUY":
            target_dollars = PositionSizing.calculate_kelly(
                win_rate=win_rate,
                win_loss_ratio=win_loss_ratio,
                account_size=total_value
            )
            if target_dollars <= 0.0:
                return None
            proposed_shares = PositionSizing.calculate_shares(target_dollars, current_price)
            if proposed_shares <= 0:
                return None

            eval_res = self.risk_manager.evaluate_order(
                symbol=symbol,
                side="buy",
                shares=proposed_shares,
                price=current_price,
                cash=portfolio_cash,
                portfolio_value=total_value,
                current_shares=current_shares
            )
            if not eval_res["approved"] or eval_res["adjusted_shares"] <= 0:
                return None

            return {
                "symbol": symbol,
                "side": "buy",
                "quantity": eval_res["adjusted_shares"],
                "order_type": "market"
            }

        elif movement in ("SELL", "EXIT"):
            if current_shares <= 0:
                return None

            eval_res = self.risk_manager.evaluate_order(
                symbol=symbol,
                side="sell",
                shares=current_shares,
                price=current_price,
                cash=portfolio_cash,
                portfolio_value=total_value,
                current_shares=current_shares
            )
            if not eval_res["approved"] or eval_res["adjusted_shares"] <= 0:
                return None

            return {
                "symbol": symbol,
                "side": "sell",
                "quantity": eval_res["adjusted_shares"],
                "order_type": "market"
            }

        return None

    def execute_decision(
        self,
        action: Any,
        symbol: str,
        current_price: float,
        portfolio_cash: float,
        current_shares: int = 0,
        portfolio_value: Optional[float] = None,
        win_rate: float = 0.6,
        win_loss_ratio: float = 2.0
    ) -> Optional[OrderRecord]:
        order_dict = self.translate_decision(
            action=action,
            symbol=symbol,
            current_price=current_price,
            portfolio_cash=portfolio_cash,
            current_shares=current_shares,
            portfolio_value=portfolio_value,
            win_rate=win_rate,
            win_loss_ratio=win_loss_ratio
        )
        if not order_dict:
            return None

        if not self.order_validator.validate_order(order_dict):
            logger.warning(f"Order validation failed: {order_dict}")
            return None

        order_record = self.alpaca_client.submit_order(
            symbol=order_dict["symbol"],
            quantity=order_dict["quantity"],
            side=order_dict["side"],
            order_type=order_dict.get("order_type", "market")
        )

        self.order_tracker.record_order({
            "id": order_record.id,
            "symbol": order_record.symbol,
            "quantity": order_record.quantity,
            "filled_qty": order_record.filled_qty,
            "side": order_record.side,
            "status": order_record.status,
            "price": order_record.price or current_price
        })

        return order_record



