"""Legality tests for the CardIQ payment state machine and simulator scripts."""

from __future__ import annotations

from itertools import pairwise

import pytest

from api import state_machine as sm


class TestTransitions:
    def test_happy_path_is_legal(self) -> None:
        path = [
            sm.SCHEDULED,
            sm.AUTH_PENDING,
            sm.AUTHORIZED,
            sm.PROCESSING,
            sm.RECIPIENT_PAID,
            sm.RECONCILED,
        ]
        for current, target in pairwise(path):
            sm.validate_transition(current, target)

    def test_terminal_states_admit_no_transitions(self) -> None:
        for terminal in sm.TERMINAL_STATES:
            for target in sm.ALLOWED_TRANSITIONS:
                with pytest.raises(ValueError):
                    sm.validate_transition(terminal, target)

    def test_uncertain_cannot_jump_to_recipient_paid(self) -> None:
        with pytest.raises(ValueError):
            sm.validate_transition(sm.UNCERTAIN, sm.RECIPIENT_PAID)

    def test_unknown_state_rejected(self) -> None:
        with pytest.raises(ValueError):
            sm.validate_transition("imaginary", sm.FAILED)


class TestScenarioScripts:
    def test_every_scenario_script_is_a_legal_walk(self) -> None:
        for scenario, script in sm.SCENARIOS.items():
            state = sm.SCHEDULED
            for step in script:
                sm.validate_transition(state, step.state)
                state = step.state
            assert state in sm.TERMINAL_STATES | {sm.UNCERTAIN}, scenario

    def test_uncertain_scenarios_never_end_terminal(self) -> None:
        for scenario in ("network_timeout", "unknown_auth"):
            assert sm.SCENARIOS[scenario][-1].state == sm.UNCERTAIN

    def test_decline_scenarios_end_failed(self) -> None:
        for scenario in sm.DECLINE_SCENARIOS:
            assert sm.SCENARIOS[scenario][-1].state == sm.FAILED

    def test_uncertain_message_promises_verification_not_retry(self) -> None:
        message = sm.SCENARIOS["network_timeout"][-1].message
        assert "verify the original transaction" in message

    def test_verification_paths_are_legal_from_uncertain(self) -> None:
        for confirmed in (True, False):
            state = sm.UNCERTAIN
            for step in sm.verification_steps(confirmed):
                sm.validate_transition(state, step.state)
                state = step.state
            assert state in sm.TERMINAL_STATES
