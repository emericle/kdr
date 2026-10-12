"""Backtesting harness: replay stored bars through the decision model."""
import pytest

from src.backtest import Backtester, summarize
from src.domain import Action, FullState, MarketState, PortfolioState
from src.model import BellmanEngine, LinearValueFunction, RLTrainer, portfolio_transition


def _state(price=150.0, previous=100.0, cash=1000.0, holdings=None):
    return FullState(
        market=MarketState(
            prices={"AAPL": price},
            previous_prices={"AAPL": previous},
            sentiment_scores={"AAPL": 0.5},
        ),
        portfolio=PortfolioState(cash=cash, holdings=holdings or {}),
    )


def _move(symbol="AAPL", movement="BUY"):
    return Action(symbol=symbol, movement=movement)


class TestTransition:
    @classmethod
    def test_buy_reward_is_the_price_change(cls):
        """Buying a bar that rose 50 earns 50."""
        reward, _ = portfolio_transition(_move(), _state())
        assert reward == 50.0

    @classmethod
    def test_sell_reward_flips_the_sign(cls):
        """Selling a bar that rose 50 gives up 50."""
        reward, _ = portfolio_transition(_move(movement="SELL"), _state())
        assert reward == -50.0

    @classmethod
    def test_hold_reward_is_zero(cls):
        """Holding captures nothing."""
        reward, _ = portfolio_transition(_move(movement="HOLD"), _state())
        assert reward == 0.0

    @classmethod
    def test_buy_consumes_the_position_weight(cls):
        """A quarter of 1000 cash at 150 a share buys 1.6667 shares and spends 250."""
        _, nxt = portfolio_transition(_move(), _state())
        assert nxt.portfolio.holdings["AAPL"] == pytest.approx(1000.0 * 0.25 / 150.0)
        assert nxt.portfolio.cash == pytest.approx(750.0)

    @classmethod
    def test_sell_returns_the_position_to_cash(cls):
        """Selling a 2 share position at 100 puts 200 back into cash."""
        state = _state(price=100.0, previous=100.0, cash=1000.0, holdings={"AAPL": 2.0})
        _, nxt = portfolio_transition(_move(movement="SELL"), state)
        assert nxt.portfolio.holdings["AAPL"] == pytest.approx(0.0)
        assert nxt.portfolio.cash == pytest.approx(1200.0)

    @classmethod
    def test_sell_is_capped_at_the_position(cls):
        """A SELL never hands back more than the position that is held."""
        state = _state(price=100.0, previous=100.0, cash=1000.0, holdings={"AAPL": 0.5})
        _, nxt = portfolio_transition(_move(movement="SELL"), state)
        assert nxt.portfolio.holdings["AAPL"] == pytest.approx(0.0)
        assert nxt.portfolio.cash == pytest.approx(1050.0)

    @classmethod
    def test_transition_returns_the_next_state(cls):
        """The successor carries the prices this step just observed."""
        _, nxt = portfolio_transition(_move(), _state())
        assert nxt.market.previous_prices["AAPL"] == 150.0

    @classmethod
    def test_transition_rejects_an_action_without_a_symbol(cls):
        """A movement that names nothing cannot be scored against a price."""
        with pytest.raises(ValueError, match="names a symbol"):
            portfolio_transition(Action(movement="BUY"), _state())

    @classmethod
    def test_transition_leaves_the_input_state_alone(cls):
        """Scoring every candidate must not mutate the state being scored."""
        state = _state()
        portfolio_transition(_move(), state)
        assert state.market.previous_prices["AAPL"] == 100.0
        assert state.portfolio.cash == 1000.0


class TestBacktester:
    @classmethod
    def test_run_replays_every_bar(cls):
        """Three bars for one symbol produce three decisions."""
        bars = [
            {"timestamp": 1.0e9, "close": 100.0},
            {"timestamp": 1.0e9 + 60, "close": 110.0},
            {"timestamp": 1.0e9 + 120, "close": 90.0},
        ]
        report = Backtester(_fake_db(bars), ["AAPL"], BellmanEngine(), LinearValueFunction()).run(minutes=5)
        assert report["steps"] == 3
        assert [d["action"] for d in report["decisions"]] == ["BUY", "BUY", "SELL"]

    @classmethod
    def test_run_reports_the_final_portfolio_value(cls):
        """A rising history leaves the paper portfolio better than it started."""
        bars = [
            {"timestamp": 1.0e9, "close": 100.0},
            {"timestamp": 1.0e9 + 60, "close": 110.0},
            {"timestamp": 1.0e9 + 120, "close": 120.0},
        ]
        report = Backtester(_fake_db(bars), ["AAPL"], BellmanEngine(), LinearValueFunction()).run(minutes=5)
        assert report["initial_value"] == 100_000.0
        assert report["final_value"] > report["initial_value"]
        assert report["return"] == pytest.approx(report["final_value"] - report["initial_value"])

    @classmethod
    def test_run_with_no_bars_makes_no_moves(cls):
        """An empty history is an empty report, not a crash."""
        report = Backtester(_fake_db([]), ["AAPL"], BellmanEngine(), LinearValueFunction()).run(minutes=5)
        assert report["steps"] == 0
        assert report["return"] == 0.0

    @classmethod
    def test_run_replays_every_symbol(cls):
        """Two symbols with bars at the same times decide once per symbol per bar."""
        bars = {
            "AAPL": [
                {"timestamp": 1.0e9, "close": 100.0},
                {"timestamp": 1.0e9 + 60, "close": 110.0},
            ],
            "TSLA": [
                {"timestamp": 1.0e9, "close": 200.0},
                {"timestamp": 1.0e9 + 60, "close": 180.0},
            ],
        }
        report = Backtester(_fake_db(bars), ["AAPL", "TSLA"], BellmanEngine(), LinearValueFunction()).run(minutes=5)
        assert report["steps"] == 4
        assert [(d["symbol"], d["action"]) for d in report["decisions"]] == [
            ("AAPL", "BUY"),
            ("TSLA", "BUY"),
            ("AAPL", "BUY"),
            ("TSLA", "SELL"),
        ]

    @classmethod
    def test_summarize_reports_the_delta(cls):
        """The one line summary names the steps and the return."""
        line = summarize({"steps": 3, "initial_value": 100.0, "final_value": 120.0, "return": 20.0})
        assert line == "3 decisions, 100 -> 120 (+20)"


def _fake_db(bars):
    """Stand in for DatabaseManager so the harness can be tested without a DB."""

    class Stub:
        def get_historical_market_data(self, symbol, start_time=None):
            if isinstance(bars, dict):
                return bars.get(symbol, [])
            return bars

    return Stub()


class TestTrainingPass:
    """The same replay that reports a backtest also fits the value function."""

    @classmethod
    def test_replay_hands_the_walk_to_both_consumers(cls):
        """One pass answers both questions, so the rewards cannot disagree."""
        bars = [
            {"timestamp": 1.0e9, "close": 100.0},
            {"timestamp": 1.0e9 + 60, "close": 110.0},
            {"timestamp": 1.0e9 + 120, "close": 90.0},
        ]
        decisions, transitions = Backtester(
            _fake_db(bars), ["AAPL"], BellmanEngine(), LinearValueFunction()
        ).replay(minutes=5)

        assert len(decisions) == 3
        assert len(transitions) == 3
        assert [reward for _, reward, _ in transitions] == [d["reward"] for d in decisions]
        # One symbol: three features per symbol plus the cash term.
        assert [len(vector) for vector, _, _ in transitions] == [4, 4, 4]

    @classmethod
    def test_train_fits_the_function_the_caller_handed_it(cls):
        """Two epochs over three bars apply six TD(0) steps to the shared function."""
        bars = [
            {"timestamp": 1.0e9, "close": 100.0},
            {"timestamp": 1.0e9 + 60, "close": 110.0},
            {"timestamp": 1.0e9 + 120, "close": 120.0},
        ]
        value_fn = LinearValueFunction()
        trainer = RLTrainer(BellmanEngine(), value_fn)
        report = Backtester(
            _fake_db(bars), ["AAPL"], BellmanEngine(), value_fn
        ).train(trainer, minutes=5, epochs=2)

        assert report["epochs"] == 2
        assert report["steps"] == 6
        assert value_fn.weights is not None

    @classmethod
    def test_training_on_an_empty_history_fits_nothing(cls):
        """No bars is no steps, not a crash and not a silent weight update."""
        report = Backtester(
            _fake_db([]), ["AAPL"], BellmanEngine(), LinearValueFunction()
        ).train(RLTrainer(BellmanEngine(), LinearValueFunction()), minutes=5, epochs=3)

        assert report["steps"] == 0
        assert report["mean_reward"] == 0.0
