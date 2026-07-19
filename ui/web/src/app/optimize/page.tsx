"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AllocationExplanation,
  AllocationResult,
  DecisionCard,
  EngineCard,
  EngineConstraints,
  EngineIntent,
  EnginePurchase,
  EngineScenario,
  ExplanationLine,
  FrontierExplanation,
  FrontierResult,
  GOAL_LABELS,
  GOAL_ORDER,
  GoalKey,
  ParseIntentResult,
  WhatIfExplanation,
  WhatIfResult,
  allocateMonth,
  fetchDemoScenario,
  money,
  moneyShort,
  parseIntent,
  pct,
  runWhatIf,
  sampleFrontier,
} from "@/lib/api";
import { Badge, EmptyState, PageHeader, ProgressBar, utilizationTone } from "@/components/ui";

const SARAH_GOAL =
  "I'm applying for a mortgage in 3 months so I need to keep my credit utilization low, " +
  "but I'd still like to hit my Amex Gold spend bonus, and I pay $2,200 rent.";

function toneIcon(tone: ExplanationLine["tone"]): { glyph: string; className: string } {
  if (tone === "positive") return { glyph: "✓", className: "text-emerald-600" };
  if (tone === "caution") return { glyph: "!", className: "text-amber-600" };
  return { glyph: "•", className: "text-slate-400" };
}

function LineList({ lines }: { lines: ExplanationLine[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className="space-y-1.5">
      {lines.map((line, i) => {
        const { glyph, className } = toneIcon(line.tone);
        return (
          <li key={`${line.label}-${i}`} className="flex gap-2 text-[13px] leading-5 text-slate-600">
            <span className={`mt-px w-3 shrink-0 text-center font-bold ${className}`}>{glyph}</span>
            <span>{line.text}</span>
          </li>
        );
      })}
    </ul>
  );
}

function DecisionCardView({
  card,
  purchase,
  highlighted,
}: {
  card: DecisionCard;
  purchase?: EnginePurchase;
  highlighted: boolean;
}) {
  return (
    <div className={`panel p-5 ${highlighted ? "ring-2 ring-indigo-200" : ""}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-900">{card.card_name}</span>
            {purchase?.is_recurring ? <Badge tone="teal">recurring</Badge> : null}
          </div>
          <p className="mt-0.5 text-[13px] text-slate-500">{card.headline}</p>
        </div>
        {purchase ? (
          <span className="shrink-0 text-right text-sm font-semibold text-slate-900">
            {money(purchase.amount_cents)}
          </span>
        ) : null}
      </div>
      <LineList lines={card.factor_lines} />
      {card.constraint_lines.length > 0 ? (
        <div className="mt-3 border-t border-slate-100 pt-3">
          <LineList lines={card.constraint_lines} />
        </div>
      ) : null}
      {card.alternative ? (
        <div className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-[13px] text-slate-600">
          <span className="font-semibold text-slate-500">Why not {card.alternative.card_name}? </span>
          {card.alternative.summary}
        </div>
      ) : null}
    </div>
  );
}

function CardUtilization({
  result,
  cards,
}: {
  result: AllocationResult;
  cards: EngineCard[];
}) {
  const byId = new Map(cards.map((c) => [c.id, c]));
  return (
    <div className="panel p-5">
      <h3 className="mb-4 text-sm font-semibold text-slate-900">Per-card outcome</h3>
      <div className="space-y-4">
        {result.card_summaries.map((s) => {
          const card = byId.get(s.card_id);
          const bonusTotal = s.bonus_progress_cents + s.bonus_remaining_cents;
          return (
            <div key={s.card_id}>
              <div className="mb-1 flex items-center justify-between text-[13px]">
                <span className="font-medium text-slate-700">{card?.name ?? s.card_id}</span>
                <span className="font-semibold text-slate-900">{pct(s.ending_utilization_bps)}</span>
              </div>
              <ProgressBar
                value={s.ending_utilization_bps / 100}
                tone={utilizationTone(s.ending_utilization_bps)}
              />
              <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
                <span>
                  {s.assigned_purchase_ids.length} charge
                  {s.assigned_purchase_ids.length === 1 ? "" : "s"} · {moneyShort(s.assigned_spend_cents)}
                </span>
                {bonusTotal > 0 ? (
                  <span className={s.bonus_hit ? "text-emerald-600" : ""}>
                    bonus {s.bonus_hit ? "hit ✓" : `${moneyShort(s.bonus_progress_cents)} / ${moneyShort(bonusTotal)}`}
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function OptimizePage() {
  const [scenario, setScenario] = useState<EngineScenario | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [goalText, setGoalText] = useState(SARAH_GOAL);
  const [weights, setWeights] = useState<Record<GoalKey, number>>();
  const [constraints, setConstraints] = useState<EngineConstraints>({
    max_utilization_bps: null,
    max_utilization_until: null,
    must_hit_bonus_card_ids: [],
  });
  const [parseMeta, setParseMeta] = useState<ParseIntentResult | null>(null);
  const [parsing, setParsing] = useState(false);

  const [alloc, setAlloc] = useState<{ result: AllocationResult; explanation: AllocationExplanation } | null>(null);
  const [frontier, setFrontier] = useState<{ result: FrontierResult; explanation: FrontierExplanation } | null>(null);
  const [whatIf, setWhatIf] = useState<{ result: WhatIfResult; explanation: WhatIfExplanation } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [wiPurchase, setWiPurchase] = useState("");
  const [wiCard, setWiCard] = useState("");

  useEffect(() => {
    fetchDemoScenario()
      .then((s) => {
        setScenario(s);
        setWeights(s.intent.weights);
        setConstraints(s.intent.constraints);
      })
      .catch((e) => setLoadError((e as Error).message));
  }, []);

  // The optimizer explores every card, so it plans from unlocked purchases.
  const unlockedPurchases = useMemo<EnginePurchase[]>(
    () => (scenario ? scenario.purchases.map((p) => ({ ...p, locked_card_id: null })) : []),
    [scenario],
  );

  const intent = useMemo<EngineIntent | null>(
    () => (weights ? { weights, constraints } : null),
    [weights, constraints],
  );

  const purchaseById = useMemo(
    () => new Map(unlockedPurchases.map((p) => [p.id, p])),
    [unlockedPurchases],
  );

  const bonusCards = useMemo(
    () => (scenario ? scenario.cards.filter((c) => c.signup_bonus != null) : []),
    [scenario],
  );

  const runParse = useCallback(async () => {
    if (!scenario || !goalText.trim()) return;
    setParsing(true);
    setError(null);
    try {
      const res = await parseIntent(goalText, scenario.cards, scenario.reference_date);
      setParseMeta(res);
      if (res.intent) {
        setWeights(res.intent.weights);
        setConstraints(res.intent.constraints);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setParsing(false);
    }
  }, [scenario, goalText]);

  const runAllocate = useCallback(async () => {
    if (!scenario || !intent) return;
    setBusy("allocate");
    setError(null);
    try {
      setAlloc(await allocateMonth(scenario.cards, unlockedPurchases, intent, "ilp"));
      setFrontier(null);
      setWhatIf(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [scenario, intent, unlockedPurchases]);

  const runFrontier = useCallback(async () => {
    if (!scenario || !intent) return;
    setBusy("frontier");
    setError(null);
    try {
      setFrontier(await sampleFrontier(scenario.cards, unlockedPurchases, intent, 4));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [scenario, intent, unlockedPurchases]);

  const runWhatIfNow = useCallback(async () => {
    if (!scenario || !intent || !wiPurchase || !wiCard) return;
    setBusy("whatif");
    setError(null);
    try {
      setWhatIf(await runWhatIf(scenario.cards, unlockedPurchases, intent, wiPurchase, wiCard));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }, [scenario, intent, unlockedPurchases, wiPurchase, wiCard]);

  const setWeight = (goal: GoalKey, value: number) =>
    setWeights((w) => (w ? { ...w, [goal]: value } : w));

  const toggleBonusCard = (cardId: string) =>
    setConstraints((c) => ({
      ...c,
      must_hit_bonus_card_ids: c.must_hit_bonus_card_ids.includes(cardId)
        ? c.must_hit_bonus_card_ids.filter((id) => id !== cardId)
        : [...c.must_hit_bonus_card_ids, cardId],
    }));

  const weightTotal = weights ? GOAL_ORDER.reduce((sum, g) => sum + weights[g], 0) : 0;

  if (loadError) {
    return (
      <>
        <PageHeader title="Optimizer" />
        <EmptyState message="Could not load the demo scenario" hint={loadError} />
      </>
    );
  }
  if (!scenario || !weights) {
    return (
      <>
        <PageHeader title="Optimizer" />
        <p className="text-sm text-slate-400">Loading synthetic scenario…</p>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Monthly optimizer"
        subtitle="Type a goal in plain English, then let the deterministic engine route this month's payments across every card under your weighted objectives and hard constraints. All money math is integer-cents and every result names its solver status."
      />

      {error ? (
        <div className="panel mb-6 border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">{error}</div>
      ) : null}

      {/* 1. Goal → parsed intent */}
      <div className="panel mb-6 p-5">
        <label className="label">Your goal</label>
        <textarea
          className="field min-h-[84px] resize-y"
          value={goalText}
          onChange={(e) => setGoalText(e.target.value)}
        />
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button className="btn-primary" onClick={runParse} disabled={parsing}>
            {parsing ? "Parsing…" : "Parse goal"}
          </button>
          <button className="btn-secondary" onClick={() => setGoalText(SARAH_GOAL)}>
            Reset to Sarah&apos;s goal
          </button>
        </div>
        {parseMeta ? (
          parseMeta.used_fallback ? (
            <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
              <span className="font-semibold">Fallback in use.</span> No trained intent model is
              configured, so equal weights and no hard constraints were applied. Adjust the sliders
              and constraints below to steer the plan manually.
            </div>
          ) : (
            <div className="mt-3 text-[13px] text-slate-500">
              Parsed by <span className="font-medium text-slate-700">{parseMeta.provider_name}</span>{" "}
              ({parseMeta.source}).
            </div>
          )
        ) : null}
      </div>

      {/* 2. Weights + constraints */}
      <div className="mb-6 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="panel p-5">
          <h3 className="mb-4 text-sm font-semibold text-slate-900">Objective weights</h3>
          <div className="space-y-3">
            {GOAL_ORDER.map((goal) => {
              const share = weightTotal > 0 ? (weights[goal] / weightTotal) * 100 : 0;
              return (
                <div key={goal} className="grid grid-cols-[130px_1fr_44px] items-center gap-3">
                  <span className="text-[13px] font-medium text-slate-700">{GOAL_LABELS[goal]}</span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round(weights[goal] * 100)}
                    onChange={(e) => setWeight(goal, Number(e.target.value) / 100)}
                    className="accent-indigo-600"
                  />
                  <span className="text-right text-[12px] tabular-nums text-slate-500">
                    {share.toFixed(0)}%
                  </span>
                </div>
              );
            })}
          </div>
          <p className="mt-3 text-[11px] text-slate-400">
            Relative weights; the engine normalizes them to integer parts-per-million before scoring.
          </p>
        </div>

        <div className="panel p-5">
          <h3 className="mb-4 text-sm font-semibold text-slate-900">Hard constraints</h3>
          <label className="flex items-center gap-2 text-[13px] font-medium text-slate-700">
            <input
              type="checkbox"
              className="accent-indigo-600"
              checked={constraints.max_utilization_bps != null}
              onChange={(e) =>
                setConstraints((c) => ({
                  ...c,
                  max_utilization_bps: e.target.checked ? 2000 : null,
                }))
              }
            />
            Cap per-card utilization
          </label>
          {constraints.max_utilization_bps != null ? (
            <div className="mt-2 flex items-center gap-2">
              <input
                type="number"
                min={1}
                max={100}
                value={constraints.max_utilization_bps / 100}
                onChange={(e) =>
                  setConstraints((c) => ({
                    ...c,
                    max_utilization_bps: Math.round(Number(e.target.value) * 100),
                  }))
                }
                className="field !w-24"
              />
              <span className="text-[13px] text-slate-500">% ceiling</span>
            </div>
          ) : null}

          {bonusCards.length > 0 ? (
            <div className="mt-4">
              <span className="label">Must-hit signup bonus</span>
              <div className="mt-1 space-y-1.5">
                {bonusCards.map((card) => (
                  <label
                    key={card.id}
                    className="flex items-center gap-2 text-[13px] font-medium text-slate-700"
                  >
                    <input
                      type="checkbox"
                      className="accent-indigo-600"
                      checked={constraints.must_hit_bonus_card_ids.includes(card.id)}
                      onChange={() => toggleBonusCard(card.id)}
                    />
                    {card.name}
                  </label>
                ))}
              </div>
            </div>
          ) : null}

          <div className="mt-4 flex flex-wrap gap-1.5">
            {constraints.max_utilization_bps != null ? (
              <Badge tone="teal">≤ {pct(constraints.max_utilization_bps)} per card</Badge>
            ) : null}
            {constraints.must_hit_bonus_card_ids.map((id) => (
              <Badge key={id} tone="sky">
                must hit {scenario.cards.find((c) => c.id === id)?.name ?? id}
              </Badge>
            ))}
          </div>
        </div>
      </div>

      <div className="mb-6 flex flex-wrap gap-2">
        <button className="btn-primary" onClick={runAllocate} disabled={busy != null}>
          {busy === "allocate" ? "Planning…" : "Plan my month"}
        </button>
        <button className="btn-secondary" onClick={runFrontier} disabled={busy != null || !alloc}>
          {busy === "frontier" ? "Sampling…" : "Sampled strategies"}
        </button>
      </div>

      {/* 3. Allocation */}
      {alloc ? (
        <div className="mb-8">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold text-slate-900">This month&apos;s plan</h2>
            <Badge tone={alloc.result.status === "optimal" ? "green" : "amber"} dot>
              {alloc.result.status} · {alloc.result.solver_method}
            </Badge>
          </div>

          {alloc.result.metrics ? (
            <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Projected rewards" value={money(alloc.result.metrics.projected_reward_value_cents)} />
              <Stat label="Peak utilization" value={pct(alloc.result.metrics.max_card_utilization_bps)} />
              <Stat label="Cashflow value" value={money(alloc.result.metrics.cashflow_value_cents)} />
              <Stat label="Bonuses hit" value={String(alloc.result.metrics.signup_bonus_hit_count)} />
            </div>
          ) : null}

          {alloc.explanation.failure ? (
            <div className="panel border-rose-200 bg-rose-50 p-5">
              <p className="font-semibold text-rose-700">{alloc.explanation.failure.headline}</p>
              <div className="mt-2">
                <LineList lines={alloc.explanation.failure.lines} />
              </div>
            </div>
          ) : (
            <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
              <div className="space-y-4">
                {alloc.explanation.decision_cards.map((card) => (
                  <DecisionCardView
                    key={card.purchase_id}
                    card={card}
                    purchase={purchaseById.get(card.purchase_id)}
                    highlighted={alloc.explanation.highlighted_purchase_ids.includes(card.purchase_id)}
                  />
                ))}
              </div>
              <div className="space-y-6">
                <CardUtilization result={alloc.result} cards={scenario.cards} />
                {/* 4. What-if lives beside the plan */}
                <div className="panel p-5">
                  <h3 className="mb-3 text-sm font-semibold text-slate-900">What if…</h3>
                  <div className="space-y-2">
                    <select className="field" value={wiPurchase} onChange={(e) => setWiPurchase(e.target.value)}>
                      <option value="">Move which payment?</option>
                      {unlockedPurchases.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.category} · {money(p.amount_cents)}
                        </option>
                      ))}
                    </select>
                    <select className="field" value={wiCard} onChange={(e) => setWiCard(e.target.value)}>
                      <option value="">…onto which card?</option>
                      {scenario.cards.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="btn-secondary w-full"
                      onClick={runWhatIfNow}
                      disabled={busy != null || !wiPurchase || !wiCard}
                    >
                      {busy === "whatif" ? "Reoptimizing…" : "Run what-if"}
                    </button>
                  </div>
                  {whatIf ? (
                    <div className="mt-4 border-t border-slate-100 pt-3">
                      <p className="mb-2 text-[13px] font-semibold text-slate-700">
                        {whatIf.explanation.headline}
                      </p>
                      <LineList lines={whatIf.explanation.delta_lines} />
                      {whatIf.explanation.changed_assignment_lines.length > 0 ? (
                        <div className="mt-3">
                          <LineList lines={whatIf.explanation.changed_assignment_lines} />
                        </div>
                      ) : null}
                      {whatIf.explanation.failure ? (
                        <p className="mt-2 text-[13px] text-rose-600">
                          {whatIf.explanation.failure.headline}
                        </p>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          )}
        </div>
      ) : null}

      {/* 5. Sampled strategy frontier */}
      {frontier ? (
        <div className="mb-8">
          <div className="mb-2 flex flex-wrap items-center gap-3">
            <h2 className="text-lg font-semibold text-slate-900">Sampled strategies</h2>
            <Badge tone="slate">
              {frontier.result.successful_solves}/{frontier.result.grid_size} weightings solved
            </Badge>
          </div>
          <p className="mb-4 max-w-3xl text-[13px] text-slate-500">
            {frontier.explanation.disclosure_lines.map((l) => l.text).join(" ")}
          </p>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {frontier.result.points.map((point, i) => {
              const expl = frontier.explanation.points[i];
              return (
                <div key={point.label} className="panel p-5">
                  <div className="mb-2 flex items-center gap-2">
                    <Badge tone="teal">{point.label}</Badge>
                  </div>
                  {expl ? <LineList lines={expl.metric_lines} /> : null}
                  <div className="mt-3 flex flex-wrap gap-1">
                    {GOAL_ORDER.filter((g) => (point.weights_ppm[g] ?? 0) > 0).map((g) => (
                      <span key={g} className="text-[11px] text-slate-400">
                        {GOAL_LABELS[g]} {Math.round((point.weights_ppm[g] ?? 0) / 10000)}%
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <p className="mt-8 text-[11px] text-slate-400">
        Synthetic accounts and purchases; public issuer product terms only. No real money, credentials, or PII.
      </p>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel px-4 py-3">
      <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-slate-400">{label}</div>
      <div className="mt-1 text-lg font-semibold text-slate-900">{value}</div>
    </div>
  );
}
