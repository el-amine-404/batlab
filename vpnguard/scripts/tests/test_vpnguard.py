#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "vpnguard.py"
SPEC = importlib.util.spec_from_file_location("vpnguard", SCRIPT)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)

NOW = 1_800_000_000.0


class DecideTests(unittest.TestCase):
    def test_one_zero_waits_and_the_second_reconnects(self) -> None:
        state = guard.State(last_port=38694)
        self.assertFalse(guard.decide(state, True, 0, NOW).reconnect)
        self.assertTrue(guard.decide(state, True, 0, NOW + 300).reconnect)

    def test_a_port_resets_the_count(self) -> None:
        state = guard.State(zero_runs=1, last_port=60162)
        plan = guard.decide(state, True, 60162, NOW)
        self.assertEqual((plan.reconnect, plan.reannounce, state.zero_runs), (False, False, 0))

    def test_a_changed_port_reannounces_but_the_first_one_seen_does_not(self) -> None:
        self.assertTrue(guard.decide(guard.State(last_port=38694), True, 60162, NOW).reannounce)
        self.assertFalse(guard.decide(guard.State(), True, 60162, NOW).reannounce)

    def test_a_tunnel_that_is_down_is_left_to_gluetun(self) -> None:
        state = guard.State(zero_runs=5)
        plan = guard.decide(state, False, 0, NOW)
        self.assertEqual((plan.reconnect, state.zero_runs), (False, 0))

    def test_three_reconnects_an_hour_then_one_give_up_message(self) -> None:
        state = guard.State(zero_runs=1, reconnects=[NOW - 3000, NOW - 2000, NOW - 1000])
        first = guard.decide(state, True, 0, NOW)
        self.assertEqual((first.reconnect, first.give_up), (False, True))
        again = guard.decide(state, True, 0, NOW + 300)
        self.assertEqual((again.reconnect, again.give_up), (False, False))
        later = guard.decide(state, True, 0, NOW + 3000)
        self.assertTrue(later.reconnect)


if __name__ == "__main__":
    unittest.main()
