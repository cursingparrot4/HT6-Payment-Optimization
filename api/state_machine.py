"""Payment lifecycle state machine and failure simulator for CardIQ.

Every synthetic transaction walks a scripted path through explicit states.
Transitions are validated against ``ALLOWED_TRANSITIONS``; the simulator can
only produce sequences that the state machine permits. Uncertain outcomes are
never auto-retried — they park in ``status_uncertain`` until verification.
"""

from __future__ import annotations

from dataclasses import dataclass

SCHEDULED = "scheduled"
AUTH_PENDING = "authorization_pending"
AUTHORIZED = "authorized"
PROCESSING = "processing"
RECIPIENT_PAID = "recipient_paid"
RECONCILED = "reconciled"
FAILED = "failed"
UNCERTAIN = "status_uncertain"

TERMINAL_STATES = {RECONCILED, FAILED}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    SCHEDULED: {AUTH_PENDING, FAILED},
    AUTH_PENDING: {AUTHORIZED, FAILED, UNCERTAIN},
    AUTHORIZED: {PROCESSING, FAILED},
    PROCESSING: {RECIPIENT_PAID, FAILED},
    RECIPIENT_PAID: {RECONCILED},
    UNCERTAIN: {AUTHORIZED, FAILED},
    RECONCILED: set(),
    FAILED: set(),
}


@dataclass(frozen=True)
class Step:
    state: str
    message: str


SUCCESS_TAIL = [
    Step(PROCESSING, "Authorization approved; funds capture in progress."),
    Step(RECIPIENT_PAID, "Recipient paid. The synthetic charge posted to the card."),
    Step(RECONCILED, "Payment reconciled against the card statement. Cycle complete."),
]

SCENARIOS: dict[str, list[Step]] = {
    "success": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(AUTHORIZED, "Card issuer authorized the charge."),
        *SUCCESS_TAIL,
    ],
    "card_declined": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(FAILED, "Issuer declined the authorization (synthetic decline code 05)."),
    ],
    "insufficient_credit": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(FAILED, "Declined: the charge exceeds the card's available credit."),
    ],
    "card_locked": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(FAILED, "Declined: the card is locked by the cardholder."),
    ],
    "card_expired": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(FAILED, "Declined: the card expired before the charge date."),
    ],
    "network_timeout": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(
            UNCERTAIN,
            "Network timeout during authorization. Payment status is uncertain. CardIQ "
            "will verify the original transaction before attempting another charge.",
        ),
    ],
    "unknown_auth": [
        Step(AUTH_PENDING, "Contacting the card network for authorization…"),
        Step(
            UNCERTAIN,
            "The processor returned an unknown authorization status. Payment status is "
            "uncertain. CardIQ will verify the original transaction before attempting "
            "another charge.",
        ),
    ],
}

DECLINE_SCENARIOS = {"card_declined", "insufficient_credit", "card_locked", "card_expired"}


def validate_transition(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current)
    if allowed is None:
        raise ValueError(f"Unknown state: {current}")
    if target not in allowed:
        raise ValueError(f"Illegal transition {current} -> {target}")


def next_step(scenario: str, step_index: int) -> Step | None:
    script = SCENARIOS[scenario]
    if step_index >= len(script):
        return None
    return script[step_index]


def verification_steps(confirmed: bool) -> list[Step]:
    """Resolution path out of ``status_uncertain`` after verifying the original charge."""

    if confirmed:
        return [
            Step(
                AUTHORIZED,
                "Verification found the original authorization. Resuming the existing "
                "charge — no second charge was created.",
            ),
            *SUCCESS_TAIL,
        ]
    return [
        Step(
            FAILED,
            "Verification confirmed the original charge never completed. It is now safe "
            "to retry with a new idempotency key.",
        )
    ]
