from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from time import monotonic

from data.loaders import load_eval_probes, load_product_catalog, load_scenario
from engine.config import EngineConfig
from engine.ilp import allocate_ilp
from engine.models import Goal, Intent, OptimizationStatus, SolverMethod
from engine.optimize import allocate_month, recommend_purchase


def one_hot(goal: Goal) -> Intent:
    return Intent(weights={candidate: int(candidate is goal) for candidate in Goal})


def assignment_map(result) -> dict[str, str]:
    return {assignment.purchase_id: assignment.card_id for assignment in result.assignments}


def assert_integer_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith(("_cents", "_millicents", "_bps", "_day")):
                assert isinstance(child, int) and not isinstance(child, bool)
            assert_integer_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_integer_fields(child)


def winners(probe, goals: Iterable[Goal]) -> set[str]:
    return {
        result.winner.card_id
        for goal in goals
        if (
            result := recommend_purchase(probe.cards, probe.purchase, one_hot(goal))
        ).winner
        is not None
    }


def test_catalog_contains_the_eight_agreed_products_with_official_sources() -> None:
    catalog = load_product_catalog()

    assert [product.id for product in catalog.products] == [
        "rbc-ion-plus",
        "rbc-avion-visa-infinite",
        "td-rewards-visa",
        "td-aeroplan-visa-infinite",
        "amex-cobalt",
        "amex-gold-rewards",
        "scotia-momentum-visa-infinite",
        "rogers-red-world-elite",
    ]
    assert catalog.verified_on == date(2026, 7, 18)
    assert all(product.sources for product in catalog.products)
    assert all(product.point_value_basis for product in catalog.products)
    assert all(product.unmodeled_terms for product in catalog.products)
    assert_integer_fields(catalog.model_dump())


def test_sarah_scenario_has_only_synthetic_account_and_purchase_state() -> None:
    loaded = load_scenario()
    scenario = loaded.scenario

    assert scenario.synthetic is True
    assert loaded.persona_label == "Sarah (synthetic)"
    assert len(scenario.cards) == 4
    assert len(scenario.purchases) == 20
    assert sum(purchase.amount_cents for purchase in scenario.purchases) == 589_000
    rents = [
        purchase
        for purchase in scenario.purchases
        if purchase.is_recurring and purchase.category == "rent"
    ]
    assert len(rents) == 1
    assert rents[0].amount_cents == 220_000
    assert all(
        purchase.date.year == 2026 and purchase.date.month == 8
        for purchase in scenario.purchases
    )
    assert set(loaded.demo_intents) == {"mortgage", "travel"}
    assert all(loaded.account_assumptions[card.id] for card in scenario.cards)
    assert_integer_fields(scenario.model_dump())


def test_sarah_mortgage_and_travel_greedy_plans_are_feasible_and_change() -> None:
    loaded = load_scenario()
    mortgage = allocate_month(
        loaded.scenario.cards,
        loaded.scenario.purchases,
        loaded.demo_intents["mortgage"],
        SolverMethod.GREEDY,
    )
    travel = allocate_month(
        loaded.scenario.cards,
        loaded.scenario.purchases,
        loaded.demo_intents["travel"],
        SolverMethod.GREEDY,
    )

    assert mortgage.status is OptimizationStatus.HEURISTIC
    assert travel.status is OptimizationStatus.HEURISTIC
    assert mortgage.metrics is not None
    assert mortgage.metrics.max_card_utilization_bps <= 3_000
    changed = [
        purchase_id
        for purchase_id, card_id in assignment_map(mortgage).items()
        if assignment_map(travel)[purchase_id] != card_id
    ]
    assert len(changed) >= 3


def test_sarah_exact_requests_are_bounded_and_return_verified_plans() -> None:
    loaded = load_scenario()
    config = EngineConfig(
        ilp_timeout_seconds=3,
        ilp_wall_timeout_seconds=10,
    )

    for preset_name in ("mortgage", "travel"):
        started = monotonic()
        result = allocate_ilp(
            loaded.scenario.cards,
            loaded.scenario.purchases,
            loaded.demo_intents[preset_name],
            config,
            include_alternatives=False,
        )
        elapsed = monotonic() - started

        assert elapsed < 15
        assert result.successful
        assert result.status in {
            OptimizationStatus.OPTIMAL,
            OptimizationStatus.HEURISTIC_FALLBACK,
        }
        assert result.metrics is not None
        if preset_name == "mortgage":
            assert result.metrics.max_card_utilization_bps <= 3_000


def test_eval_probes_are_intent_sensitive() -> None:
    probes = load_eval_probes()
    matrix = {
        probe.id: winners(
            probe,
            [
                Goal.MAX_CASHBACK,
                Goal.MAX_TRAVEL,
                Goal.CREDIT_HEALTH,
                Goal.HIT_SIGNUP_BONUS,
                Goal.MAX_CASHFLOW,
                Goal.MIN_RISK,
            ],
        )
        for probe in probes
    }

    assert len(probes) == 5
    assert all(len(card_ids) >= 2 for card_ids in matrix.values())
