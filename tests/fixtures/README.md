# Shared Test Fixtures

Keep small, hand-checkable builders and immutable JSON/schema snapshots here. Fixtures return fresh objects and use fixed 2026 dates. Do not import production scoring helpers to calculate expected values, store secrets/provider responses from real users, or make the entire test suite depend on the full Sarah scenario.

Planned groups:

- `cards.py`: minimal cashback, points, bonus, zero-limit, and low-capacity cards.
- `scenarios.py`: tiny feasible, repairable, unresolved, and provably infeasible cases.
- `engine_results.py`: distinctive typed result objects for explanation tests.
- `intent_outputs.py`: raw valid/malformed model strings and provider failures.
- `schema/`: reviewed Pydantic/OpenAPI snapshots after contracts are implemented.
