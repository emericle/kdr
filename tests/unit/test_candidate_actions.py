"""Tests for the Bellman decision action space."""


from src.domain import TradeMovement, candidate_actions


class TestCandidateActions:
    def test_no_symbols_offers_no_actions(self):
        assert candidate_actions([], {}) == []

    def test_one_symbol_offers_three_actions(self):
        actions = candidate_actions(["AAPL"], {})

        assert len(actions) == 3
        assert all(action.symbol == "AAPL" for action in actions)
        assert [action.movement for action in actions] == [
            TradeMovement.BUY,
            TradeMovement.SELL,
            TradeMovement.HOLD,
        ]

    def test_actions_enumerate_symbol_then_movement(self):
        actions = candidate_actions(["AAPL", "MSFT"], {})

        assert [(action.symbol, action.movement) for action in actions] == [
            ("AAPL", TradeMovement.BUY),
            ("AAPL", TradeMovement.SELL),
            ("AAPL", TradeMovement.HOLD),
            ("MSFT", TradeMovement.BUY),
            ("MSFT", TradeMovement.SELL),
            ("MSFT", TradeMovement.HOLD),
        ]

    def test_holding_a_symbol_still_offers_all_movements(self):
        weights = {"AAPL": 1.0}
        actions = candidate_actions(["AAPL"], weights)

        assert len(actions) == 3
        assert all(action.target_weights == weights for action in actions)

    def test_current_weights_are_copied_not_aliased(self):
        """The caller's dict is not captured by reference, so later edits cannot reach back."""
        weights = {"AAPL": 1.0}
        actions = candidate_actions(["AAPL"], weights)

        weights["AAPL"] = 0.0
        assert actions[0].target_weights == {"AAPL": 1.0}

    def test_actions_are_distinct_objects(self):
        actions = candidate_actions(["AAPL"], {})

        assert len({id(action) for action in actions}) == len(actions)
