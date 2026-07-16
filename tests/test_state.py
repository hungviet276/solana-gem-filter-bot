from __future__ import annotations

import unittest

from gem_filter.domain.models import Decision, TokenStatus
from gem_filter.domain.state import status_for_decision, transition


class StateTests(unittest.TestCase):
    def test_forward_state_machine(self) -> None:
        self.assertEqual(transition(TokenStatus.WATCH, TokenStatus.EARLY), TokenStatus.EARLY)
        self.assertEqual(transition(TokenStatus.EARLY, TokenStatus.STRONG), TokenStatus.STRONG)
        self.assertEqual(
            transition(TokenStatus.STRONG, TokenStatus.CONFIRMED), TokenStatus.CONFIRMED
        )

    def test_closed_cannot_reopen_live(self) -> None:
        with self.assertRaises(ValueError):
            transition(TokenStatus.CLOSED, TokenStatus.STRONG)

    def test_backtest_replay_can_override_transition_guard(self) -> None:
        self.assertEqual(
            transition(TokenStatus.CLOSED, TokenStatus.WATCH, replay=True), TokenStatus.WATCH
        )

    def test_decision_mapping(self) -> None:
        self.assertEqual(status_for_decision(Decision.STRONG), TokenStatus.STRONG)
        self.assertEqual(status_for_decision(Decision.REJECTED), TokenStatus.REJECTED)
        self.assertIsNone(status_for_decision(Decision.NONE))


if __name__ == "__main__":
    unittest.main()
