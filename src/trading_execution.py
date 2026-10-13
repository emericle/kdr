import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from src.domain import Action, FullState, candidate_actions
from src.model import POSITION_WEIGHT, BellmanEngine, LinearValueFunction, portfolio_transition
from src.times import now_utc

logger = logging.getLogger("TradingExecution")

# What a broker round trip can fail with: a transport or HTTP error, a body that
# is not JSON, and the field conversions this file does on the parsed payload.
BROKER_FAILURES = (requests.RequestException, ValueError, TypeError, KeyError, AttributeError)

# A dashboard push is best effort: the buffer is a singleton that may be absent.
BUFFER_FAILURES = (AttributeError, TypeError, ValueError, KeyError)

# Import web_server for decision streaming
try:
    from src.web_server import decision_buffer
    WEB_SERVER_AVAILABLE = True
except ImportError:
    WEB_SERVER_AVAILABLE = False


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
    ) -> dict[str, Any]:
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
                "reason": (
                    "Order approved"
                    if adjusted_shares == shares
                    else "Shares adjusted down to comply with risk limits"
                )
            }

        if normalized_side == "sell":
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

        return {
            "approved": False,
            "adjusted_shares": 0,
            "reason": f"Unsupported order side: {side}"
        }


class OrderValidator:
    """Validates trade orders before routing to execution."""

    @staticmethod
    def validate_order(order: dict[str, Any]) -> bool:
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
        return side.lower() in ("buy", "sell")


class OrderTracker:
    """Maintains in-memory log of executed orders and tracks net positions in O(1)."""

    def __init__(self, max_history: int = 10000):
        self._history: list[dict[str, Any]] = []
        self._positions: dict[str, float] = {}
        self.max_history = max_history

    def record_order(self, order: dict[str, Any]) -> dict[str, Any]:
        record = dict(order)
        if "timestamp" not in record:
            record["timestamp"] = now_utc()
        self._history.append(record)
        if len(self._history) > self.max_history:
            self._history = self._history[-self.max_history:]

        status = str(record.get("status", "submitted")).lower()
        if status in ("rejected", "canceled", "cancelled", "expired", "failed"):
            return record

        sym = record.get("symbol")
        if sym:
            qty = float(record.get("filled_qty", record.get("quantity", record.get("qty", 0))))
            side = str(record.get("side", "")).lower()
            if side == "buy":
                self._positions[sym] = round(self._positions.get(sym, 0.0) + qty, 6)
            elif side == "sell":
                self._positions[sym] = round(self._positions.get(sym, 0.0) - qty, 6)

        return record

    def get_order_history(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if symbol is None:
            return list(self._history)
        return [o for o in self._history if o.get("symbol") == symbol]

    def get_positions(self) -> dict[str, float]:
        """Returns current net positions in O(1) time complexity."""
        return dict(self._positions)

    def get_position(self, symbol: str) -> float:
        """Returns net position for a symbol in O(1)."""
        return self._positions.get(symbol, 0.0)

    def clear(self) -> None:
        self._history.clear()
        self._positions.clear()


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
    price: float | None = None


class AlpacaClient:
    """
    Client for placing orders and querying account data via Alpaca REST API
    with connection pooling.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        base_url: str | None = None,
        session: requests.Session | None = None
    ):
        self.api_key: str = api_key or os.getenv("ALPACA_API_KEY") or ""
        self.api_secret: str = api_secret or os.getenv("ALPACA_SECRET_KEY") or ""
        base: str = base_url or os.getenv("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets/v2"
        self.base_url: str = base.rstrip("/")
        self.session: requests.Session = session or requests.Session()
        self.session.headers.update(self._headers())

    def _headers(self) -> dict[str, str]:
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
        limit_price: float | None = None
    ) -> OrderRecord:
        url = f"{self.base_url}/orders"
        payload: dict[str, Any] = {
            "symbol": symbol,
            "qty": str(quantity),
            "side": side.lower(),
            "type": order_type.lower(),
            "time_in_force": time_in_force
        }
        if limit_price is not None:
            payload["limit_price"] = str(limit_price)

        try:
            resp = self.session.post(url, json=payload, timeout=10)
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
        except BROKER_FAILURES as e:
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

    def get_account(self) -> dict[str, Any]:
        url = f"{self.base_url}/account"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return {}
        except BROKER_FAILURES as e:
            logger.error(f"Error getting account from Alpaca: {e}")
            return {}

    def get_positions(self) -> list[dict[str, Any]]:
        url = f"{self.base_url}/positions"
        try:
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return []
        except BROKER_FAILURES as e:
            logger.error(f"Error getting positions from Alpaca: {e}")
            return []


class TradingExecutor:
    """
    Translates model decisions into risk-managed orders and executes them with Alpaca.
    """

    def __init__(
        self,
        alpaca_client: AlpacaClient,
        risk_manager: RiskManager | None = None,
        order_tracker: OrderTracker | None = None,
        order_validator: OrderValidator | None = None,
        engine: BellmanEngine | None = None,
        value_function: LinearValueFunction | None = None,
        position_weight: float = POSITION_WEIGHT,
        cash: float = 100_000.0,
    ):
        self.alpaca_client = alpaca_client
        self.risk_manager = risk_manager or RiskManager()
        self.order_tracker = order_tracker or OrderTracker()
        self.order_validator = order_validator or OrderValidator()
        # The model the spec asks for, not a placeholder: a decision is the argmax
        # of the scored action space, and the value function is the one the
        # training loop fits (see :meth:`RLTrainer.train_step`). Zero weights mean
        # "score on the immediate reward only", which is a working policy, not a
        # crash, so a process that has not loaded trained weights still decides.
        self.engine = engine or BellmanEngine()
        self.value_function = value_function or LinearValueFunction()
        self.position_weight = position_weight
        self.cash = cash

    def decide(
        self,
        state: FullState,
        symbol: str | None = None,
    ) -> tuple[Action, dict[str, Any]]:
        """Ask the Bellman engine what to do with one state.

        The action space is :func:`candidate_actions` for the symbol the state is
        about, the reward model is :func:`portfolio_transition` (the same one the
        backtest replays), and the winner is the argmax of ``r + γ·V(s')``. When
        that argmax ties with HOLD the executor holds instead: the engine is a
        pure argmax and keeps the first candidate, which is BUY, but an
        indifferent portfolio manager does not buy.

        Args:
            state: The state to decide on, as built by
                :meth:`StateMapper.build_decision_state`.
            symbol: The symbol to decide for; required when the state carries
                more than one.

        Returns:
            The chosen action and a report with the action name, a confidence,
            the winning score and every candidate's score. The confidence is how
            far the winner is ahead of the runner-up as a fraction of the whole
            spread, so an indifferent state scores 0.5 and a state where one
            action dominates scores near 1.0.

        Raises:
            ValueError: If the symbol to decide for cannot be determined from the
                state.
        """
        if symbol is None:
            if len(state.symbols) != 1:
                raise ValueError(
                    f"decide needs one symbol when the state carries {len(state.symbols)}"
                )
            symbol = state.symbols[0]
        elif symbol not in state.market.prices:
            raise ValueError(f"The state carries no price for {symbol}")

        candidates = candidate_actions([symbol], {})
        scored = self.engine.score_actions(
            state,
            state.symbols,
            candidates,
            lambda action, current: portfolio_transition(action, current, self.position_weight),
            self.value_function.predict,
        )
        best_action, best_score = max(scored, key=lambda pair: pair[1])

        # An indifferent state is not an instruction to trade. The engine's argmax
        # keeps the earlier candidate on a tie, which would be BUY because that is
        # the order the action space is enumerated in; a portfolio manager who is
        # indifferent holds, so a tie with HOLD's score decides HOLD. The engine
        # itself stays a pure argmax, and this is the executor's policy.
        hold = next((pair for pair in scored if pair[0].movement == "HOLD"), None)
        if hold is not None and best_score - hold[1] <= 1e-12:
            best_action, best_score = hold

        values = [score for _, score in scored]
        spread = max(values) - min(values)
        if spread > 0.0:
            runner_up = sorted(values)[-2]
            confidence = 0.5 + 0.5 * (best_score - runner_up) / spread
        else:
            # Every candidate scores the same: the model has nothing to say, and
            # 0.5 is the midpoint of the confidence the dashboard expects.
            confidence = 0.5

        return best_action, {
            "symbol": symbol,
            "action": self._movement_name(best_action),
            "confidence": round(confidence, 2),
            "score": best_score,
            "values": {self._movement_name(action): score for action, score in scored},
        }

    def _movement_name(self, action: Action) -> str:
        """The movement an :class:`Action` carries, as the string the dashboard expects.

        ``Action.movement`` is optional because an action can also be continuous
        (target weights), so the field has to be unwrapped here rather than at
        every use site. An action that names no movement is a hold.
        """
        return action.movement.value if action.movement is not None else "HOLD"

    def _movement_of(self, action: Any) -> str | None:
        """The trade movement as an uppercase string, however the action carries it."""
        if hasattr(action, "movement") and action.movement is not None:
            return str(action.movement.value if hasattr(action.movement, "value") else action.movement).upper()
        if hasattr(action, "value"):
            return str(action.value).upper()
        if isinstance(action, str):
            return action.upper()
        return None

    def _push_decision(self, symbol: str, action: str, confidence: float, reasoning: list[str]) -> None:
        """Tell the dashboard about a decision. A missing dashboard is not an error."""
        if not (WEB_SERVER_AVAILABLE and decision_buffer):
            return
        try:
            from src.web_server import TradingDecision
            decision_buffer.add_decision(TradingDecision(
                symbol=symbol,
                action=action,
                confidence=confidence,
                reasoning=reasoning,
                timestamp=now_utc()
            ))
        except BUFFER_FAILURES as e:
            logger.debug(f"Error pushing decision to buffer: {e}")

    def _translate_buy(
        self,
        symbol: str,
        current_price: float,
        portfolio_cash: float,
        current_shares: int,
        total_value: float,
        win_rate: float,
        win_loss_ratio: float,
        confidence: float | None = None
    ) -> dict[str, Any] | None:
        """Size a BUY with Kelly, let the risk manager cut it down, and order what survives.

        Args:
            confidence: The model's confidence for this action, as :meth:`decide`
                reported it. It is what the dashboard is told, and it is told only
                once an order exists: a decision the risk manager refused is not one
                the dashboard should show.
        """
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

        # One record per order, after the risk manager approved it. The fraction of
        # the proposed size that survived is a fact about sizing, so it goes in the
        # reasoning; the confidence field is the model's belief about the action.
        decided_by = (
            "Bellman model decided this action"
            if confidence is not None
            else "Action translated without a model report"
        )
        order = {
            "symbol": symbol,
            "side": "buy",
            "quantity": eval_res["adjusted_shares"],
            "order_type": "market"
        }
        # The record describes what will actually be submitted, so it is pushed
        # only for an order that survived sizing and the validator: a decision the
        # risk manager refused leaves nothing, and one order is one record rather
        # than two.
        if self.order_validator.validate_order(order):
            self._push_decision(
                symbol,
                "BUY",
                confidence if confidence is not None else 0.5,
                [
                    decided_by,
                    f"Risk manager kept {eval_res['adjusted_shares']} of {proposed_shares} proposed shares",
                ],
            )
        return order

    def _translate_sell(
        self,
        symbol: str,
        current_price: float,
        portfolio_cash: float,
        current_shares: int,
        total_value: float,
        confidence: float | None = None
    ) -> dict[str, Any] | None:
        """Size a SELL against the position already held, then let the risk manager approve it.

        Args:
            confidence: The model's confidence for this action, as :meth:`decide`
                reported it. See :meth:`_translate_buy` for when the dashboard is
                told about it.
        """
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

        # One record per order, after the risk manager approved it.
        decided_by = (
            "Bellman model decided this action"
            if confidence is not None
            else "Action translated without a model report"
        )
        order = {
            "symbol": symbol,
            "side": "sell",
            "quantity": eval_res["adjusted_shares"],
            "order_type": "market"
        }
        # The record describes what will actually be submitted.
        if self.order_validator.validate_order(order):
            self._push_decision(
                symbol,
                "SELL",
                confidence if confidence is not None else 0.5,
                [
                    decided_by,
                    f"Risk manager kept {eval_res['adjusted_shares']} of {current_shares} held shares",
                ],
            )
        return order

    def translate_decision(
        self,
        action: Any,
        symbol: str,
        current_price: float,
        portfolio_cash: float,
        current_shares: int = 0,
        portfolio_value: float | None = None,
        win_rate: float = 0.6,
        win_loss_ratio: float = 2.0,
        confidence: float | None = None
    ) -> dict[str, Any] | None:
        """Size an action into an order, and tell the dashboard about it once.

        Args:
            action: The action to translate; its movement is what the dashboard
                reports.
            confidence: The model's own confidence for this action, as :meth:`decide`
                reported it. The dashboard shows a confidence next to a decision, so
                the number it gets is the one the decision came from — a constant
                would read as if it had been measured. A caller that hands over only
                an action has not asked the model, and the reasoning says so rather
                than naming a model that did not run.

        The record is pushed by whichever sizing produced an order, and only for an
        order the validator accepts, so a decision the risk manager refused leaves
        nothing on the dashboard and one order is one record rather than two.
        """
        # Extract string representation of trade movement
        movement = self._movement_of(action)

        if movement in ("HOLD", "NONE", None):
            return None

        total_value = (
            portfolio_value
            if portfolio_value is not None
            else portfolio_cash + (current_shares * current_price)
        )

        if movement == "BUY":
            return self._translate_buy(
                symbol, current_price, portfolio_cash, current_shares, total_value, win_rate, win_loss_ratio, confidence
            )

        if movement in ("SELL", "EXIT"):
            return self._translate_sell(
                symbol, current_price, portfolio_cash, current_shares, total_value, confidence
            )

        return None

    def execute_decision(
        self,
        action: Any,
        symbol: str,
        current_price: float,
        portfolio_cash: float,
        current_shares: int = 0,
        portfolio_value: float | None = None,
        win_rate: float = 0.6,
        win_loss_ratio: float = 2.0,
        confidence: float | None = None
    ) -> OrderRecord | None:
        order_dict = self.translate_decision(
            action=action,
            symbol=symbol,
            current_price=current_price,
            portfolio_cash=portfolio_cash,
            current_shares=current_shares,
            portfolio_value=portfolio_value,
            win_rate=win_rate,
            win_loss_ratio=win_loss_ratio,
            confidence=confidence
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

        # The cash the next decision is scored against. An executed order moves it,
        # and a rejected one (no fill) leaves it alone, so a run that keeps being
        # refused does not pretend its portfolio is shrinking.
        filled = float(order_record.filled_qty or 0.0)
        if filled > 0.0:
            traded_value = filled * float(order_record.price or current_price)
            if order_record.side == "buy":
                self.cash = max(self.cash - traded_value, 0.0)
            elif order_record.side == "sell":
                self.cash += traded_value

        return order_record

    def decide_and_execute(
        self,
        state: FullState,
        symbol: str | None = None,
        win_rate: float = 0.6,
        win_loss_ratio: float = 2.0
    ) -> OrderRecord | None:
        """Decide with the Bellman engine, then execute the order that survives risk management.

        The price, cash and holdings come out of the :class:`FullState` the caller
        handed in rather than out of the arguments, so the state the model scored
        is the state the order was sized against.

        Args:
            state: The state to decide on.
            symbol: The symbol to decide for; required when the state carries
                more than one.
            win_rate: Passed to Kelly sizing.
            win_loss_ratio: Passed to Kelly sizing.

        Returns:
            The order the broker returned, or ``None`` when the model held or the
            order did not survive sizing, validation or the risk manager.
        """
        action, report = self.decide(state, symbol)
        decided = symbol if symbol is not None else state.symbols[0]
        return self.execute_decision(
            action=action,
            symbol=decided,
            current_price=state.market.prices[decided],
            portfolio_cash=state.portfolio.cash,
            current_shares=int(state.portfolio.holdings.get(decided, 0.0)),
            portfolio_value=state.portfolio_value(),
            win_rate=win_rate,
            win_loss_ratio=win_loss_ratio,
            confidence=report["confidence"],
        )



