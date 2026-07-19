"""Deterministic tests for the CardIQ card-selection layer over engine scoring."""

from __future__ import annotations

from datetime import date, timedelta

from api.recommender import build_priority_plan, build_switch_recommendation, rank_cards

TODAY = date(2026, 7, 18)


def _card(**overrides) -> dict:
    base = {
        "id": "card_x",
        "name": "Card X",
        "reward_type": "cashback",
        "reward_rate_bps": 200,
        "point_value_millicents": 1_000,
        "credit_limit_cents": 1_000_000,
        "current_balance_cents": 50_000,
        "bonus_target_cents": None,
        "bonus_progress_cents": None,
        "bonus_value_cents": None,
        "bonus_deadline": None,
        "expiry_date": (TODAY + timedelta(days=365)).isoformat(),
        "status": "active",
        "ineligible_categories": "",
        "recent_failures": 0,
    }
    base.update(overrides)
    return base


def spec_cards() -> list[dict]:
    """The canonical Card A/B/C rent scenario from the product spec."""

    return [
        _card(
            id="card_a",
            name="Aeroplan A",
            reward_type="points",
            reward_rate_bps=100,
            credit_limit_cents=600_000,
            current_balance_cents=200_000,
            bonus_target_cents=300_000,
            bonus_progress_cents=120_000,
            bonus_value_cents=60_000,
            bonus_deadline=(TODAY + timedelta(days=12)).isoformat(),
        ),
        _card(
            id="card_b",
            name="Cashback B",
            reward_rate_bps=200,
            credit_limit_cents=1_000_000,
            current_balance_cents=50_000,
        ),
        _card(
            id="card_c",
            name="Cashback C",
            reward_rate_bps=150,
            credit_limit_cents=600_000,
            current_balance_cents=120_000,
        ),
    ]


def rent_payment(**overrides) -> dict:
    base = {
        "id": "pay_rent",
        "name": "Rent",
        "category": "rent",
        "amount_cents": 240_000,
        "due_date": (TODAY + timedelta(days=5)).isoformat(),
        "frequency": "monthly",
        "processing_fee_bps": 0,
        "funding_card_id": "card_b",
    }
    base.update(overrides)
    return base


class TestSpecScenario:
    def test_bonus_completion_makes_card_a_primary(self) -> None:
        result = rank_cards(spec_cards(), rent_payment(), TODAY)
        assert result["primary_card_id"] == "card_a"
        assert result["backup_card_id"] == "card_b"
        winner = result["ranked"][0]
        assert winner["bonus_completes"] is True
        assert winner["bonus_score_cents"] == 60_000
        assert winner["reward_cents"] == 2_400  # 2,400 points at 1 cent each

    def test_after_bonus_completion_card_b_wins(self) -> None:
        cards = spec_cards()
        cards[0]["bonus_progress_cents"] = cards[0]["bonus_target_cents"]
        result = rank_cards(cards, rent_payment(), TODAY)
        assert result["primary_card_id"] == "card_b"
        by_id = {e["card_id"]: e for e in result["ranked"]}
        assert by_id["card_b"]["net_reward_cents"] == 4_800
        assert by_id["card_a"]["net_reward_cents"] == 2_400

    def test_switch_recommended_from_current_funding_card(self) -> None:
        ranking = rank_cards(spec_cards(), rent_payment(), TODAY)
        switch = build_switch_recommendation(ranking, rent_payment())
        assert switch is not None
        assert switch["from_card_id"] == "card_b"
        assert switch["to_card_id"] == "card_a"
        assert switch["delta_cents"] > 0

    def test_no_switch_when_funding_card_is_already_best(self) -> None:
        ranking = rank_cards(spec_cards(), rent_payment(funding_card_id="card_a"), TODAY)
        assert build_switch_recommendation(ranking, rent_payment(funding_card_id="card_a")) is None

    def test_bonus_completion_predicts_takeover_condition(self) -> None:
        result = rank_cards(spec_cards(), rent_payment(), TODAY)
        assert any("take over" in c or "projected" in c for c in result["change_conditions"])


class TestHardEligibility:
    def test_locked_card_is_excluded(self) -> None:
        cards = spec_cards()
        cards[0]["status"] = "locked"
        result = rank_cards(cards, rent_payment(), TODAY)
        excluded = {e["card_id"] for e in result["excluded"]}
        assert "card_a" in excluded

    def test_expired_card_is_excluded(self) -> None:
        cards = spec_cards()
        cards[0]["expiry_date"] = (TODAY - timedelta(days=1)).isoformat()
        result = rank_cards(cards, rent_payment(), TODAY)
        assert "card_a" in {e["card_id"] for e in result["excluded"]}

    def test_insufficient_credit_is_excluded(self) -> None:
        cards = spec_cards()
        cards[1]["current_balance_cents"] = 900_000  # only $1,000 available
        result = rank_cards(cards, rent_payment(), TODAY)
        assert "card_b" in {e["card_id"] for e in result["excluded"]}

    def test_ineligible_category_is_excluded(self) -> None:
        cards = spec_cards()
        cards[1]["ineligible_categories"] = "rent"
        result = rank_cards(cards, rent_payment(), TODAY)
        assert "card_b" in {e["card_id"] for e in result["excluded"]}

    def test_declined_card_excluded_and_backup_promoted(self) -> None:
        cards = spec_cards()
        cards[0]["bonus_progress_cents"] = cards[0]["bonus_target_cents"]
        result = rank_cards(cards, rent_payment(), TODAY, exclude_card_ids={"card_b"})
        assert result["primary_card_id"] != "card_b"
        assert "card_b" in {e["card_id"] for e in result["excluded"]}

    def test_no_feasible_card_yields_no_primary(self) -> None:
        cards = [
            _card(id="tiny", name="Tiny", credit_limit_cents=100_000, current_balance_cents=0)
        ]
        result = rank_cards(cards, rent_payment(), TODAY)
        assert result["primary_card_id"] is None
        assert result["ranked"] == []


class TestPenalties:
    def test_processing_fee_reduces_net_reward(self) -> None:
        cards = [_card(id="solo", name="Solo")]
        result = rank_cards(cards, rent_payment(processing_fee_bps=250), TODAY)
        evaluation = result["ranked"][0]
        assert evaluation["fee_cents"] == 6_000
        assert evaluation["net_reward_cents"] == evaluation["reward_cents"] - 6_000

    def test_failure_history_penalizes_score(self) -> None:
        clean = rank_cards([_card(id="c1", name="C1")], rent_payment(), TODAY)
        flaky = rank_cards(
            [_card(id="c1", name="C1", recent_failures=2)], rent_payment(), TODAY
        )
        assert flaky["ranked"][0]["score_cents"] < clean["ranked"][0]["score_cents"]

    def test_high_utilization_penalizes_score(self) -> None:
        low = rank_cards(
            [_card(id="c1", name="C1", current_balance_cents=0)], rent_payment(), TODAY
        )
        high = rank_cards(
            [_card(id="c1", name="C1", current_balance_cents=600_000)], rent_payment(), TODAY
        )
        assert high["ranked"][0]["score_cents"] < low["ranked"][0]["score_cents"]

    def test_ranking_is_deterministic(self) -> None:
        first = rank_cards(spec_cards(), rent_payment(), TODAY)
        second = rank_cards(spec_cards(), rent_payment(), TODAY)
        assert first == second


class TestPriorityPlan:
    def test_priority_order_reserves_scarce_best_card_capacity(self) -> None:
        cards = [
            _card(
                id="premium",
                name="Premium Rewards",
                reward_rate_bps=1000,
                credit_limit_cents=300_000,
                current_balance_cents=0,
            ),
            _card(
                id="backup",
                name="Backup Cashback",
                reward_rate_bps=100,
                credit_limit_cents=1_000_000,
                current_balance_cents=0,
            ),
        ]
        payments = [
            rent_payment(id="pay_rent", name="Rent", amount_cents=200_000),
            rent_payment(id="pay_utilities", name="Utilities", amount_cents=200_000),
        ]

        plan = build_priority_plan(cards, payments, TODAY)

        assert plan["pay_rent"]["priority_weight"] == 2
        assert plan["pay_rent"]["priority_card_id"] == "premium"
        assert plan["pay_rent"]["priority_status"] == "optimal"
        assert plan["pay_utilities"]["priority_weight"] == 1
        assert plan["pay_utilities"]["independent_best_card_id"] == "premium"
        assert plan["pay_utilities"]["priority_card_id"] == "backup"
        assert plan["pay_utilities"]["priority_status"] == "off_optimal"
        assert plan["pay_utilities"]["off_optimal_cents"] > 0
