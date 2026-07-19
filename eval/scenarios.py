"""Frozen probe suite and monthly verification scenario.

Five hand-built single-purchase probes (IMPLEMENTATION.md §10) measure decision
equivalence: whether predicted weights lead the exact engine to the same card as the
gold weights. Each probe embeds a deliberate tradeoff so different one-hot intents
produce different winners — a probe every intent answers identically would measure
nothing. The monthly scenario (§12) is one small exact-ILP allocation with enough
tension for assignments to move. All structures are hashed into the report so a
changed probe invalidates comparisons.
"""

from __future__ import annotations

from datetime import date

from engine.models import Card, Purchase, RewardRule, RewardType, SignupBonus
from eval.models import EvalModel
from intent.manifests import canonical_json, sha256_bytes


class Probe(EvalModel):
    probe_id: str
    description: str
    cards: tuple[Card, ...]
    purchase: Purchase


class MonthlyScenario(EvalModel):
    scenario_id: str
    cards: tuple[Card, ...]
    purchases: tuple[Purchase, ...]


def _card(**kwargs) -> Card:
    defaults = {
        "annual_fee_cents": 0,
        "point_value_millicents": 1_000,
        "statement_day": 1,
        "due_day": 15,
        "base_reward_type": RewardType.CASHBACK,
    }
    return Card(**{**defaults, **kwargs})


PROBES: tuple[Probe, ...] = (
    Probe(
        probe_id="rent-reward-vs-bonus",
        description="High flat cashback against completing an unmet signup bonus.",
        cards=(
            _card(
                id="p1-cashback",
                name="Probe1 Flat Cashback",
                credit_limit_cents=1_500_000,
                current_balance_cents=100_000,
                base_rate_bps=200,
            ),
            _card(
                id="p1-bonus",
                name="Probe1 Bonus Chaser",
                credit_limit_cents=1_000_000,
                current_balance_cents=100_000,
                base_rate_bps=100,
                base_reward_type=RewardType.POINTS,
                signup_bonus=SignupBonus(
                    spend_required_cents=300_000,
                    spend_so_far_cents=120_000,
                    reward_value_cents=60_000,
                    deadline_date=date(2026, 8, 31),
                ),
            ),
        ),
        purchase=Purchase(
            id="p1-rent",
            amount_cents=200_000,
            category="rent",
            date=date(2026, 8, 1),
            is_recurring=True,
        ),
    ),
    Probe(
        probe_id="grocery-rate-vs-health",
        description="Category-rate card at high utilization against a clean low-rate card.",
        cards=(
            _card(
                id="p2-grocer",
                name="Probe2 Grocery Rate",
                credit_limit_cents=300_000,
                current_balance_cents=180_000,
                base_rate_bps=100,
                reward_rules=[
                    RewardRule(category="groceries", rate_bps=400, reward_type=RewardType.CASHBACK)
                ],
            ),
            _card(
                id="p2-clean",
                name="Probe2 Clean Sheet",
                credit_limit_cents=1_200_000,
                current_balance_cents=60_000,
                base_rate_bps=100,
            ),
        ),
        purchase=Purchase(
            id="p2-groceries",
            amount_cents=60_000,
            category="groceries",
            date=date(2026, 8, 5),
            is_recurring=False,
        ),
    ),
    Probe(
        probe_id="travel-value-vs-cashback",
        description="High travel point value against straightforward cashback.",
        cards=(
            _card(
                id="p3-travel",
                name="Probe3 Voyager",
                credit_limit_cents=1_000_000,
                current_balance_cents=100_000,
                base_rate_bps=50,
                base_reward_type=RewardType.POINTS,
                point_value_millicents=2_000,
                reward_rules=[
                    RewardRule(category="travel", rate_bps=300, reward_type=RewardType.POINTS)
                ],
            ),
            _card(
                id="p3-cash",
                name="Probe3 Cash Back",
                credit_limit_cents=1_000_000,
                current_balance_cents=100_000,
                base_rate_bps=200,
            ),
        ),
        purchase=Purchase(
            id="p3-flight",
            amount_cents=80_000,
            category="travel",
            date=date(2026, 8, 10),
            is_recurring=False,
        ),
    ),
    Probe(
        probe_id="dining-reward-vs-float",
        description="Dining category rate against a much longer payment float.",
        cards=(
            _card(
                id="p4-dine",
                name="Probe4 Diner",
                credit_limit_cents=800_000,
                current_balance_cents=80_000,
                base_rate_bps=100,
                statement_day=3,
                due_day=25,
                reward_rules=[
                    RewardRule(category="dining", rate_bps=300, reward_type=RewardType.CASHBACK)
                ],
            ),
            _card(
                id="p4-float",
                name="Probe4 Long Float",
                credit_limit_cents=800_000,
                current_balance_cents=80_000,
                base_rate_bps=100,
                statement_day=25,
                due_day=20,
            ),
        ),
        purchase=Purchase(
            id="p4-dinner",
            amount_cents=30_000,
            category="dining",
            date=date(2026, 8, 2),
            is_recurring=False,
        ),
    ),
    Probe(
        probe_id="large-capacity-vs-reward",
        description="Better reward into tight headroom against ample capacity.",
        cards=(
            _card(
                id="p5-reward",
                name="Probe5 Rich Reward",
                credit_limit_cents=600_000,
                current_balance_cents=100_000,
                base_rate_bps=200,
            ),
            _card(
                id="p5-room",
                name="Probe5 Head Room",
                credit_limit_cents=2_000_000,
                current_balance_cents=100_000,
                base_rate_bps=100,
            ),
        ),
        purchase=Purchase(
            id="p5-electronics",
            amount_cents=400_000,
            category="electronics",
            date=date(2026, 8, 15),
            is_recurring=False,
        ),
    ),
)


MONTHLY_SCENARIO = MonthlyScenario(
    scenario_id="eval-monthly-v1",
    cards=(
        _card(
            id="m-bonus",
            name="Monthly Bonus",
            credit_limit_cents=900_000,
            current_balance_cents=100_000,
            base_rate_bps=100,
            base_reward_type=RewardType.POINTS,
            signup_bonus=SignupBonus(
                spend_required_cents=250_000,
                spend_so_far_cents=100_000,
                reward_value_cents=40_000,
                deadline_date=date(2026, 8, 31),
            ),
        ),
        _card(
            id="m-cash",
            name="Monthly Cash",
            credit_limit_cents=700_000,
            current_balance_cents=150_000,
            base_rate_bps=200,
        ),
        _card(
            id="m-travel",
            name="Monthly Travel",
            credit_limit_cents=1_000_000,
            current_balance_cents=100_000,
            base_rate_bps=100,
            base_reward_type=RewardType.POINTS,
            point_value_millicents=2_000,
            reward_rules=[
                RewardRule(category="travel", rate_bps=300, reward_type=RewardType.POINTS)
            ],
        ),
    ),
    purchases=(
        Purchase(
            id="m-rent", amount_cents=120_000, category="rent",
            date=date(2026, 8, 1), is_recurring=True,
        ),
        Purchase(
            id="m-groceries", amount_cents=40_000, category="groceries",
            date=date(2026, 8, 6), is_recurring=False,
        ),
        Purchase(
            id="m-flight", amount_cents=50_000, category="travel",
            date=date(2026, 8, 12), is_recurring=False,
        ),
        Purchase(
            id="m-dining", amount_cents=20_000, category="dining",
            date=date(2026, 8, 20), is_recurring=False,
        ),
    ),
)


def probe_suite_sha256() -> str:
    payload = canonical_json([probe.model_dump(mode="json") for probe in PROBES])
    return sha256_bytes(payload.encode("utf-8"))


def monthly_scenario_sha256() -> str:
    payload = canonical_json(MONTHLY_SCENARIO.model_dump(mode="json"))
    return sha256_bytes(payload.encode("utf-8"))
