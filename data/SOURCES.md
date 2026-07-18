# Card Product Sources and Modeling Notes

Verified on **2026-07-18** from official issuer pages. Product names and terms remain the property/trademarks of their respective issuers. This repository is an independent hackathon prototype and is not endorsed by any issuer.

The canonical machine-readable details are in [cards.json](cards.json). This page summarizes what was mapped and what was intentionally omitted.

| Product | Official source | Engine mapping | Important simplification |
|---|---|---|---|
| RBC ION+ Visa | [RBC](https://www.rbcroyalbank.com/credit-cards/rewards/rbc-ion-plus-visa.html) | 3 points/$ on listed everyday categories; 1 point/$ base; `$48` fee | Static `0.714` cents/point from the issuer's 1,400-points-for-$10 gift-card example |
| RBC Avion Visa Infinite | [RBC](https://www.rbcroyalbank.com/credit-cards/travel/rbc-avion-visa-infinite.html) | 1.25 points/$ travel; 1 point/$ base; `$120` fee | Static `2.0` cents/point is an optimistic travel-schedule assumption; issuer also shows lower simple-redemption value |
| TD Rewards Visa Card | [TD](https://www.td.com/ca/en/personal-banking/products/credit-cards/travel-rewards/rewards-visa-card) | 4 points/$ Expedia for TD; 3 groceries/dining/transit; 2 recurring/digital; 1 base; no fee | Separate `$5,000` annual accelerated-category caps and booking qualification are documented, not tracked |
| TD Aeroplan Visa Infinite | [TD](https://www.td.com/ca/en/personal-banking/products/credit-cards/aeroplan/aeroplan-visa-infinite-card) | 1.5 points/$ grocery/gas/EV/direct Air Canada/eligible Hyatt; 1 base; `$139` fee | Static `1.5` cents/point is a demo assumption; Aeroplan redemption value is dynamic |
| American Express Cobalt | [American Express](https://www.americanexpress.com/en-ca/credit-cards/cobalt-card/) | 5 points/$ eats/drinks (scenario maps eligible groceries); 3 streaming; 2 gas/transit/rideshare; 1 base; `$191.88` annualized fee | Merchant eligibility, caps, credits, and monthly offer milestones are omitted |
| American Express Gold Rewards | [American Express](https://www.americanexpress.com/en-ca/credit-cards/gold-rewards-card/) | 2 points/$ travel/gas/grocery/drugstore; 1 base; `$250` fee | Sarah's bonus is synthetic and does not reproduce the issuer's live monthly offer |
| Scotia Momentum Visa Infinite | [Product page](https://www.scotiabank.com/ca/en/personal/credit-cards/visa/momentum-infinite-card.html), [program terms](https://www.scotiabank.com/ca/en/personal/credit-cards/visa/momentum-infinite-card/welcome-kit/terms-conditions-momentum-infinite.html) | 4% grocery/recurring; 2% gas/EV/transit/rideshare/food delivery; 1% base; `$120` fee | Separate `$25,000` annual accelerated-rate caps, MCC classification, and issuer cashback rounding are omitted |
| Rogers Red World Elite Mastercard | [Rogers Bank](https://www.rogersbank.com/en/rogers_red_worldelite_mastercard_details) | 2% eligible-purchase base under a synthetic qualifying-service assumption; no fee | 1.5% non-customer rate, 3% USD rate, FX, and 1.5x Rogers redemption bonus require state the engine does not model |

## Scenario policy

- Limits, balances, statement dates, due dates, persona, purchases, bonus progress, and service qualification are always synthetic.
- Rent uses each card's base earn rule. It is not silently treated as an issuer recurring-payment category.
- Public welcome offers are metadata only. The engine supports one threshold bonus, while many live offers are staged or monthly.
- Point values are static assumptions used consistently for deterministic comparison, not predictions or guarantees.
- Product terms change. Update `verified_on`, source summaries, tests, and catalog hashes whenever terms are refreshed.
