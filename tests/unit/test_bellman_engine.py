"""The Bellman engine must choose an action, not just score one."""
import pytest

from src.domain import FullState, MarketState, PortfolioState, candidate_actions
from src.model import BellmanEngine


def _state(prices, previous, cash=1000.0):
    return FullState(
        market=MarketState(
            prices=prices,
            previous_prices=previous,
            sentiment_scores=dict.fromkeys(prices, 0.5),
        ),
        portfolio=PortfolioState(cash=cash, holdings={}),
    )


def _successor(state, marker):
    """A successor whose normalized vector is unmistakably ``marker``.

    The sentiment feature is the second entry of the normalized vector and is
    ``(score - 0.5) / 0.5``, so a score of ``0.5 + 0.5 * marker`` puts ``marker``
    straight into it.
    """
    return FullState(
        market=MarketState(
            prices={"AAPL": 150.0},
            previous_prices={"AAPL": 150.0},
            sentiment_scores={"AAPL": 0.5 + 0.5 * marker},
        ),
        portfolio=PortfolioState(cash=state.portfolio.cash, holdings=dict(state.portfolio.holdings)),
    )


def _transition(action, state):
    """Reward the price change the action captured, and hand a successor rated by movement."""
    price = state.market.prices[action.symbol]
    previous = state.market.previous_prices.get(action.symbol, price)
    change = price - previous
    if action.movement == "BUY":
        return change, _successor(state, 0.0)
    if action.movement == "SELL":
        return -change, _successor(state, 200.0)
    return 0.0, _successor(state, 0.0)


class TestScoreActions:
    """The engine exposes the scores as well as the argmax, so a caller can report
    how sure it is rather than only what it chose."""

    @classmethod
    def test_score_actions_returns_one_pair_per_candidate(cls):
        """Every candidate is scored, in candidate order."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        scored = BellmanEngine().score_actions(
            state, ["AAPL"], candidate_actions(["AAPL"], {}), _transition, lambda vector: 0.0
        )
        assert [action.movement for action, _ in scored] == ["BUY", "SELL", "HOLD"]
        assert [score for _, score in scored] == [50.0, -50.0, 0.0]

    @classmethod
    def test_compute_action_is_the_argmax_of_the_scores(cls):
        """The chosen action is the highest score, and a tie keeps the earlier candidate."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        candidates = candidate_actions(["AAPL"], {})
        scored = BellmanEngine().score_actions(state, ["AAPL"], candidates, _transition, lambda vector: 0.0)
        chosen = BellmanEngine().compute_action(state, ["AAPL"], candidates, _transition, lambda vector: 0.0)
        assert chosen.movement == max(scored, key=lambda pair: pair[1])[0].movement

    @classmethod
    def test_score_actions_uses_the_discount_factor(cls):
        """The discount shapes the scores, not only the choice."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        value_fn = lambda vector: float(vector[1])  # noqa: E731
        greedy = BellmanEngine(discount_factor=0.0).score_actions(
            state, ["AAPL"], candidate_actions(["AAPL"], {}), _transition, value_fn
        )
        far_sighted = BellmanEngine(discount_factor=1.0).score_actions(
            state, ["AAPL"], candidate_actions(["AAPL"], {}), _transition, value_fn
        )
        sell_greedy = next(score for action, score in greedy if action.movement == "SELL")
        sell_farsighted = next(score for action, score in far_sighted if action.movement == "SELL")
        assert sell_greedy == -50.0
        assert sell_farsighted == sell_greedy + 200.0

    @classmethod
    def test_score_actions_on_an_empty_action_space(cls):
        """No candidates is an empty report; refusing to decide is compute_action's job."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        assert BellmanEngine().score_actions(state, ["AAPL"], [], _transition, lambda vector: 0.0) == []


class TestComputeAction:
    """The engine must choose an action, not just score one."""

    @classmethod
    def test_compute_action_holds_with_the_best_greedy_reward(cls):
        """With every successor worth zero, the engine keeps the greedy reward."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        action = BellmanEngine().compute_action(
            state, ["AAPL"], candidate_actions(["AAPL"], {}), _transition, lambda vector: 0.0
        )
        assert action.movement == "BUY"
        assert action.symbol == "AAPL"

    @classmethod
    def test_compute_action_prefers_the_higher_target_q(cls):
        """A value function that rates SELL's successor far higher wins even when its reward is worse."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        action = BellmanEngine().compute_action(
            state,
            ["AAPL"],
            candidate_actions(["AAPL"], {}),
            _transition,
            lambda vector: float(vector[1]),
        )
        assert action.movement == "SELL"

    @classmethod
    def test_discount_factor_changes_the_choice(cls):
        """A discount of zero ignores the future; a discount of one does not."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        candidates = candidate_actions(["AAPL"], {})

        greedy = BellmanEngine(discount_factor=0.0).compute_action(
            state, ["AAPL"], candidates, _transition, lambda vector: float(vector[1])
        )
        far_sighted = BellmanEngine(discount_factor=1.0).compute_action(
            state, ["AAPL"], candidates, _transition, lambda vector: float(vector[1])
        )
        assert greedy.movement == "BUY"
        assert far_sighted.movement == "SELL"

    @classmethod
    def test_compute_action_rejects_an_empty_action_space(cls):
        """No candidates means no decision, not a silent HOLD."""
        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        with pytest.raises(ValueError) as exc:
            BellmanEngine().compute_action(state, ["AAPL"], [], _transition, lambda vector: 0.0)
        assert "at least one candidate" in exc.value.args[0]

    @classmethod
    def test_value_function_is_called_once_per_candidate(cls):
        """Scoring must not re-evaluate the same successor twice."""
        calls = []

        def value_fn(vector):
            calls.append(len(vector))
            return 0.0

        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        BellmanEngine().compute_action(
            state, ["AAPL"], candidate_actions(["AAPL"], {}), _transition, value_fn
        )
        assert calls == [4, 4, 4]

    @classmethod
    def test_transition_is_asked_for_every_candidate(cls):
        """The engine evaluates the action space, it does not sample it."""
        seen = []

        def transition(action, state):
            seen.append(action.movement)
            return 0.0, state

        state = _state({"AAPL": 150.0}, {"AAPL": 100.0})
        BellmanEngine().compute_action(state, ["AAPL"], candidate_actions(["AAPL"], {}), transition, lambda v: 0.0)
        assert seen == ["BUY", "SELL", "HOLD"]

    @classmethod
    def test_state_vector_is_the_configured_length(cls):
        """Every candidate is scored against a vector of 3 symbols * 3 + 1 features."""
        vectors = []

        def value_fn(vector):
            vectors.append(len(vector))
            return 0.0

        state = _state({"AAPL": 150.0, "TSLA": 300.0}, {"AAPL": 150.0, "TSLA": 300.0})
        BellmanEngine().compute_action(
            state, ["AAPL", "TSLA"], candidate_actions(["AAPL"], {}), _transition, value_fn
        )
        assert vectors == [7, 7, 7]
