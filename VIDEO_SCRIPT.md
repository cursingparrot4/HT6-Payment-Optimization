# CardIQ — 2-Minute Video Script

> Target: ~2:00. Spoken narration in **bold**, on-screen actions in _italics_.
> Demo the **Optimizer** page (wired to the real engine), not the DB dashboard.

---

## [0:00–0:20] Hook + problem

**Most people pick a credit card with one crude rule: whatever has the highest cashback. They ignore what actually moves the needle — credit utilization, statement-date float, and time-bound bonus deadlines. And rent, the biggest recurring expense, is the biggest lever of all.**

**Meet Sarah. Four cards, and one real constraint: she's applying for a mortgage in three months, she still wants to hit her Amex Gold bonus, and she pays twenty-two hundred dollars in rent.**

_On screen: portfolio of Sarah's 4 synthetic cards._

---

## [0:20–1:20] The engine (the centerpiece)

**Here's what makes CardIQ different. The core is a deterministic optimization engine — not machine learning.**

**Reward on a card is amount times rate. That's an exact calculation, not a prediction. A learned approximation of a solver is strictly less correct — a liability on a financial-correctness track. So we solve for the math and use ML only for language.**

**Every money value is integer cents — no floating point anywhere in the money path.**

**The heart of it is multi-card monthly allocation under real constraints. Credit limits, a utilization ceiling that holds until her mortgage date, and her must-hit bonus are hard constraints. Cashback, travel value, and cashflow float are soft objectives. We keep that line clean and explicit.**

**We solve it two ways: a greedy baseline, then an exact integer program with CBC. And we're honest about it — a result is only labeled "optimal" when the solver proves it. Infeasible inputs return a structured diagnosis, never a crash.**

_On screen: run "Plan my month" → decision cards appear._

**And instead of one answer, we surface the sampled strategy frontier — max cashback, best credit health, balanced — the real tradeoffs, with an honest disclosure that it's a bounded weight sweep.**

---

## [1:20–1:45] Live reactive demo

_On screen: point at a decision card._

**Every line here comes from the solver's real numbers — projected rewards, utilization staying under her ceiling, bonus progress, and why the runner-up lost.**

_On screen: change the goal to "maximize travel," re-run._

**Now watch. Mortgage's done — maximize travel. Re-run, and the entire allocation reshapes. Rent moves to a different card, live.**

---

## [1:45–2:00] ML tie-in + close

**A small fine-tuned model turns Sarah's plain-English goal into those weights and constraints. And the same solver verifies the model — feed its weights in, check the recommendation matches the gold answer. The solver does the math and grades the AI.**

**Every explanation is templated from the solver's real output — faithful by construction, it can't hallucinate. Calculations, not predictions. That's CardIQ.**

---

### Emphasis checklist (say these no matter what)
- Deterministic engine — "calculations, not predictions"
- Integer cents, no floats
- Hard constraints vs. soft objectives
- `optimal` only when proven; structured `infeasible`, never a crash
- The solver doubles as the ML verifier

### Don't
- Don't do more than one what-if or tour every feature
- Don't claim a complete Pareto frontier or attach "confidence" scores to exact math
