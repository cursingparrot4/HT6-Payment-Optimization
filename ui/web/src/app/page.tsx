"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Badge, ProgressBar, utilizationTone } from "@/components/ui";
import {
  DashboardData,
  IntentProviderName,
  ParsedIntent,
  ParseIntentResult,
  api,
  money,
  moneyShort,
  parseIntent,
  pct,
  savePaymentPriorities,
} from "@/lib/api";

const ALERT_TONES: Record<string, "teal" | "amber" | "rose" | "sky"> = {
  switch: "teal",
  bonus_deadline: "amber",
  card_locked: "rose",
  card_expired: "rose",
  uncertain: "sky",
};

type DashboardPayment = DashboardData["payments"][number];
type GoalKey = keyof ParsedIntent["weights"];

const GOAL_LABELS: Record<GoalKey, string> = {
  max_cashback: "Cashback",
  max_travel: "Travel",
  credit_health: "Credit health",
  hit_signup_bonus: "Bonus",
  max_cashflow: "Cashflow",
  min_risk: "Headroom",
};

const DEFAULT_GOAL_TEXT =
  "I'm applying for a mortgage soon, so keep utilization low, but I still want to hit my bonus.";

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [priorityIds, setPriorityIds] = useState<string[]>([]);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [prioritySaving, setPrioritySaving] = useState(false);
  const [priorityMessage, setPriorityMessage] = useState<string | null>(null);
  const [goalText, setGoalText] = useState(DEFAULT_GOAL_TEXT);
  const [intentResult, setIntentResult] = useState<ParseIntentResult | null>(null);
  const [intentLoading, setIntentLoading] = useState(false);
  const [intentError, setIntentError] = useState<string | null>(null);
  const [intentProvider, setIntentProvider] = useState<IntentProviderName>("auto");

  const load = useCallback(async () => {
    try {
      setData(await api<DashboardData>("/dashboard"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!data) return;
    const incomingIds = data.payments.map(({ payment }) => payment.id);
    setPriorityIds((current) => {
      const retained = current.filter((id) => incomingIds.includes(id));
      const missing = incomingIds.filter((id) => !retained.includes(id));
      return [...retained, ...missing];
    });
  }, [data]);

  const savePriorityOrder = useCallback(
    async (nextIds: string[]) => {
      setPriorityIds(nextIds);
      setPrioritySaving(true);
      setPriorityMessage(null);
      try {
        await savePaymentPriorities(nextIds);
        await load();
        setPriorityMessage("Saved");
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPrioritySaving(false);
      }
    },
    [load],
  );

  const movePriority = useCallback(
    (fromId: string, toId: string) => {
      if (fromId === toId) return;
      const fromIndex = priorityIds.indexOf(fromId);
      const toIndex = priorityIds.indexOf(toId);
      if (fromIndex < 0 || toIndex < 0) return;
      const next = [...priorityIds];
      const [moved] = next.splice(fromIndex, 1);
      next.splice(toIndex, 0, moved);
      void savePriorityOrder(next);
    },
    [priorityIds, savePriorityOrder],
  );

  const nudgePriority = useCallback(
    (paymentId: string, direction: -1 | 1) => {
      const index = priorityIds.indexOf(paymentId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= priorityIds.length) return;
      const next = [...priorityIds];
      [next[index], next[target]] = [next[target], next[index]];
      void savePriorityOrder(next);
    },
    [priorityIds, savePriorityOrder],
  );

  const reseed = async () => {
    setSeeding(true);
    try {
      await api("/seed", { method: "POST" });
      await load();
    } finally {
      setSeeding(false);
    }
  };

  const parseGoal = async (event: FormEvent) => {
    event.preventDefault();
    if (!data) return;
    setIntentLoading(true);
    setIntentError(null);
    try {
      const result = await parseIntent({
        text: goalText,
        reference_date: new Date().toISOString().slice(0, 10),
        card_context: data.cards.map((card) => ({
          id: card.id,
          name: card.name,
          has_active_bonus:
            Boolean(card.bonus_target_cents && card.bonus_deadline) &&
            (card.bonus_progress_cents ?? 0) < (card.bonus_target_cents ?? 0),
        })),
        allow_fallback: true,
        provider: intentProvider,
      });
      setIntentResult(result);
    } catch (e) {
      setIntentError((e as Error).message);
    } finally {
      setIntentLoading(false);
    }
  };

  if (error) {
    return (
      <div className="panel p-6 text-sm text-rose-700">
        Could not reach the SwitchPay API ({error}). Start it with{" "}
        <code className="rounded bg-rose-50 px-1">
          .venv/bin/uvicorn api.main:app --port 8000
        </code>
        .
      </div>
    );
  }
  if (!data) return <p className="text-sm text-slate-400">Loading dashboard...</p>;

  const switches = data.payments.filter((p) => p.switch !== null);
  const topSwitch = switches[0] ?? null;
  const priorityById = new Map(data.payments.map((entry) => [entry.payment.id, entry]));
  const priorityEntries = priorityIds
    .map((paymentId) => priorityById.get(paymentId))
    .filter((entry): entry is DashboardPayment => Boolean(entry));
  const totalOffOptimal = data.payments.reduce(
    (sum, entry) => sum + (entry.off_optimal_cents ?? 0),
    0,
  );
  const optimalCount = data.payments.filter((entry) => entry.priority_status === "optimal").length;

  return (
    <div className="space-y-5">
      <header className="relative overflow-hidden rounded-[34px] bg-[linear-gradient(135deg,#343a4f,#5867d8)] p-5 text-white shadow-[0_28px_72px_-46px_rgba(52,58,79,0.9)] sm:p-6">
        <div className="pointer-events-none absolute inset-y-0 right-0 w-1/2 bg-[linear-gradient(115deg,rgba(255,255,255,0),rgba(255,255,255,0.14))]" />
        <div className="pointer-events-none absolute -right-16 top-0 h-full w-72 rotate-12 bg-white/8 blur-2xl" />
        <div className="relative flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-[15px] bg-white/18 px-3 py-1.5 text-xs font-semibold text-white ring-1 ring-white/25">
              <span className="h-2 w-2 rounded-full bg-[#aeb8ff]" />
              Live sandbox
            </span>
            {prioritySaving ? (
              <span className="rounded-[15px] bg-white/18 px-3 py-1.5 text-xs font-semibold text-white ring-1 ring-white/25">
                Saving order
              </span>
            ) : null}
            {priorityMessage ? (
              <span className="rounded-[15px] bg-white/18 px-3 py-1.5 text-xs font-semibold text-white ring-1 ring-white/25">
                {priorityMessage}
              </span>
            ) : null}
          </div>
          <h1 className="font-display text-[32px] font-semibold leading-tight text-white sm:text-[42px]">
            Payment routing
          </h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-white/76">
            Review bill priority, card routes, rewards, fees, and exceptions from one place.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {topSwitch ? (
            <Link href={`/payments/${topSwitch.payment.id}`} className="inline-flex items-center justify-center rounded-[18px] bg-white px-4 py-2.5 text-sm font-semibold text-[#465bd8] shadow-[0_18px_38px_-28px_rgba(0,0,0,0.8)] transition hover:bg-[#f2f4ff]">
              Review switch
            </Link>
          ) : null}
          <button className="inline-flex items-center justify-center rounded-[18px] border border-white/25 bg-white/14 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-white/20 disabled:cursor-not-allowed disabled:opacity-50" onClick={reseed} disabled={seeding}>
            {seeding ? "Resetting..." : "Reset demo"}
          </button>
        </div>
        </div>
      </header>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <MetricCard label="Payments" value={String(data.totals.payment_count)} />
        <MetricCard label="Cards" value={String(data.totals.card_count)} />
        <MetricCard
          label="Est. rewards"
          value={money(data.totals.estimated_reward_cents)}
          tone="text-emerald-700"
        />
        <MetricCard
          label="Est. fees"
          value={money(data.totals.estimated_fee_cents)}
          tone="text-amber-700"
        />
        <MetricCard
          label="Off optimal"
          value={money(totalOffOptimal)}
          tone={totalOffOptimal > 0 ? "text-amber-700" : "text-emerald-700"}
        />
      </section>

      <GoalParserPanel
        text={goalText}
        result={intentResult}
        loading={intentLoading}
        error={intentError}
        provider={intentProvider}
        onText={setGoalText}
        onProvider={setIntentProvider}
        onSubmit={parseGoal}
      />

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="panel overflow-hidden p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3 px-1">
            <div>
              <h2 className="text-base font-semibold text-[#202332]">Priority order</h2>
              <p className="mt-0.5 text-xs text-[#73798a]">
                {optimalCount} of {data.payments.length} routes match the independent optimum
              </p>
            </div>
            <Badge tone={totalOffOptimal > 0 ? "amber" : "green"}>
              {totalOffOptimal > 0 ? `${money(totalOffOptimal)} off` : "All optimal"}
            </Badge>
          </div>
          <div className="space-y-3">
            {priorityEntries.map((entry, index) => (
              <PriorityRow
                key={entry.payment.id}
                entry={entry}
                index={index}
                total={priorityEntries.length}
                dragging={draggingId === entry.payment.id}
                saving={prioritySaving}
                onDragStart={() => setDraggingId(entry.payment.id)}
                onDragEnd={() => setDraggingId(null)}
                onDrop={() => {
                  if (draggingId) movePriority(draggingId, entry.payment.id);
                  setDraggingId(null);
                }}
                onMoveUp={() => nudgePriority(entry.payment.id, -1)}
                onMoveDown={() => nudgePriority(entry.payment.id, 1)}
              />
            ))}
          </div>
        </div>

        <RouteReview entry={topSwitch ?? data.payments[0]} />
      </section>

      <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="panel overflow-hidden">
          <div className="border-b border-white/70 px-5 py-4">
            <h2 className="text-base font-semibold text-[#202332]">Upcoming payments</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-left text-sm">
              <thead className="bg-[#f4f5f8] text-xs font-semibold text-[#73798a]">
                <tr>
                  <th className="px-4 py-3">Payment</th>
                  <th className="px-4 py-3">Due</th>
                  <th className="px-4 py-3 text-right">Amount</th>
                  <th className="px-4 py-3">Funding card</th>
                  <th className="px-4 py-3">Recommended</th>
                  <th className="px-4 py-3 text-right">Net</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.payments.map((entry) => (
                  <PaymentRow key={entry.payment.id} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-5">
          <AlertsPanel alerts={data.alerts} />
          <CardsPanel cards={data.cards} />
        </div>
      </section>
    </div>
  );
}

function GoalParserPanel({
  text,
  result,
  loading,
  error,
  provider,
  onText,
  onProvider,
  onSubmit,
}: {
  text: string;
  result: ParseIntentResult | null;
  loading: boolean;
  error: string | null;
  provider: IntentProviderName;
  onText: (text: string) => void;
  onProvider: (provider: IntentProviderName) => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const intent = result?.intent;
  const sourceTone =
    result?.source === "gemini"
      ? "green"
      : result?.source === "freesolo"
        ? "sky"
      : result?.source === "fallback"
        ? "amber"
        : "teal";

  return (
    <section className="panel grid gap-4 p-4 lg:grid-cols-[minmax(0,0.95fr)_minmax(360px,1fr)]">
      <form onSubmit={onSubmit} className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-base font-semibold text-[#202332]">Goal parser</h2>
          <Badge tone="slate">language to weights</Badge>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-[#73798a]">Provider</label>
          <select
            className="w-full rounded-[14px] border border-[#dde2eb] bg-white px-3 py-2 text-sm font-medium text-[#202332] outline-none transition focus:border-[#465bd8]"
            value={provider}
            onChange={(event) => onProvider(event.target.value as IntentProviderName)}
          >
            <option value="auto">Auto</option>
            <option value="freesolo">Freesolo trained model</option>
            <option value="gemini">Gemini prompted model</option>
            <option value="fixture">Local fixture</option>
          </select>
        </div>
        <textarea
          className="min-h-[104px] w-full resize-none rounded-[18px] border border-[#dde2eb] bg-[#f7f8fb] px-4 py-3 text-sm leading-6 text-[#202332] outline-none transition focus:border-[#465bd8] focus:bg-white"
          value={text}
          onChange={(event) => onText(event.target.value)}
        />
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="submit"
            disabled={loading || text.trim().length === 0}
            className="inline-flex items-center justify-center rounded-[16px] bg-[#465bd8] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#3849b7] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Parsing..." : "Parse goal"}
          </button>
          {result ? <Badge tone={sourceTone}>{result.source}</Badge> : null}
          {result?.used_fallback ? <Badge tone="amber">fallback</Badge> : null}
          {error ? <span className="text-xs font-medium text-rose-700">{error}</span> : null}
        </div>
      </form>

      <div className="rounded-[22px] border border-[#dde2eb] bg-white p-4">
        {intent ? (
          <div className="space-y-4">
            <div>
              <div className="mb-2 flex items-center justify-between">
                <p className="text-xs font-medium text-[#73798a]">Optimizer weights</p>
                <p className="text-xs text-[#9aa1b2]">
                  {result?.valid_model_output ? "validated" : "fallback settings"}
                </p>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {(Object.keys(GOAL_LABELS) as GoalKey[]).map((goal) => (
                  <IntentWeight key={goal} label={GOAL_LABELS[goal]} value={intent.weights[goal]} />
                ))}
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              {intent.constraints.max_utilization_bps !== null ? (
                <Badge tone="green">
                  util {"<="} {pct(intent.constraints.max_utilization_bps)}
                </Badge>
              ) : (
                <Badge tone="slate">no utilization cap</Badge>
              )}
              {intent.constraints.max_utilization_until ? (
                <Badge tone="sky">until {intent.constraints.max_utilization_until}</Badge>
              ) : null}
              {intent.constraints.must_hit_bonus_card_ids.map((cardId) => (
                <Badge key={cardId} tone="amber">
                  must hit {cardId}
                </Badge>
              ))}
            </div>

            {result?.warnings.length ? (
              <p className="text-xs leading-5 text-[#73798a]">
                {result.warnings.map((warning) => warning.message).join(" ")}
              </p>
            ) : null}
          </div>
        ) : (
          <div className="flex h-full min-h-[150px] items-center justify-center rounded-[18px] bg-[#f7f8fb] px-5 text-center text-sm leading-6 text-[#73798a]">
            Type a goal to see the exact Intent JSON shape the optimizer will use.
          </div>
        )}
      </div>
    </section>
  );
}

function IntentWeight({ label, value }: { label: string; value: number }) {
  const percent = Math.round(value * 100);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-3 text-xs">
        <span className="font-medium text-[#555d70]">{label}</span>
        <span className="tabular text-[#73798a]">{percent}%</span>
      </div>
      <ProgressBar value={percent} tone={percent >= 35 ? "emerald" : "teal"} />
    </div>
  );
}

function PriorityRow({
  entry,
  index,
  total,
  dragging,
  saving,
  onDragStart,
  onDragEnd,
  onDrop,
  onMoveUp,
  onMoveDown,
}: {
  entry: DashboardPayment;
  index: number;
  total: number;
  dragging: boolean;
  saving: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  const { payment, priority_status, priority_card_name, independent_best_card_name } = entry;
  const offOptimal = entry.off_optimal_cents ?? 0;
  const priorityCard = compactCardName(priority_card_name);
  const independentCard = compactCardName(independent_best_card_name);
  const routeMatches = priorityCard === independentCard;
  const statusTone =
    priority_status === "optimal" ? "green" : priority_status === "infeasible" ? "rose" : "amber";

  return (
    <article
      draggable={!saving}
      onDragStart={(event) => {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", payment.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      }}
      onDrop={(event) => {
        event.preventDefault();
        onDrop();
      }}
      className={`grid gap-3 rounded-[22px] border border-white/85 px-4 py-3 shadow-[0_14px_34px_-30px_rgba(35,48,74,0.5)] transition md:grid-cols-[42px_minmax(180px,1fr)_minmax(250px,1.25fr)_120px_76px] md:items-center ${
        dragging ? "bg-[#f1f3ff] opacity-70" : "bg-white hover:bg-[#fbfbfd]"
      }`}
      aria-label={`Priority ${index + 1}: ${payment.name}`}
    >
      <div className="flex items-center gap-2 text-[#9aa1b2]">
        <span className="grid h-8 w-8 place-items-center rounded-[12px] bg-[#465bd8] text-xs font-semibold text-white shadow-[0_10px_22px_-16px_rgba(70,91,216,0.85)]">
          {index + 1}
        </span>
        <span className="grid grid-cols-2 gap-0.5 text-[#c2c7d3]" aria-hidden>
          {Array.from({ length: 6 }).map((_, dot) => (
            <span key={dot} className="h-1 w-1 rounded-full bg-current" />
          ))}
        </span>
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate font-semibold text-[#202332]">{payment.name}</h3>
          <Badge tone="slate">{payment.category}</Badge>
        </div>
        <p className="mt-1 text-xs text-[#73798a]">
          Due {payment.due_date} · {money(payment.amount_cents)} · weight x{entry.priority_weight}
        </p>
      </div>

      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={statusTone}>{priority_status.replace("_", " ")}</Badge>
          <span className="text-xs font-medium text-[#73798a]">
            {routeMatches ? "Matches best card" : "Priority tradeoff"}
          </span>
        </div>
        <p className="mt-1 truncate text-sm font-medium text-[#555d70]">
          {routeMatches ? (
            priorityCard
          ) : (
            <>
              {priorityCard} <span className="text-[#9aa1b2]">instead of</span> {independentCard}
            </>
          )}
        </p>
      </div>

      <div className="text-left md:text-right">
        <p className="text-xs text-[#73798a]">Impact</p>
        <p className={`tabular mt-1 font-semibold ${offOptimal > 0 ? "text-amber-700" : "text-emerald-700"}`}>
          {offOptimal > 0 ? `-${money(offOptimal)}` : "$0.00"}
        </p>
      </div>

      <div className="flex gap-1 md:justify-end">
        <button
          type="button"
          onClick={onMoveUp}
          disabled={saving || index === 0}
          aria-label={`Move ${payment.name} up`}
          className="grid h-8 w-8 place-items-center rounded-[12px] border border-[#dde2eb] bg-[#f7f8fb] text-sm font-semibold text-[#73798a] hover:bg-white disabled:cursor-not-allowed disabled:opacity-35"
        >
          ↑
        </button>
        <button
          type="button"
          onClick={onMoveDown}
          disabled={saving || index === total - 1}
          aria-label={`Move ${payment.name} down`}
          className="grid h-8 w-8 place-items-center rounded-[12px] border border-[#dde2eb] bg-[#f7f8fb] text-sm font-semibold text-[#73798a] hover:bg-white disabled:cursor-not-allowed disabled:opacity-35"
        >
          ↓
        </button>
      </div>
    </article>
  );
}

function RouteReview({ entry }: { entry: DashboardPayment | null }) {
  if (!entry) {
    return (
      <aside className="panel p-4">
        <h2 className="text-sm font-semibold text-[#202332]">Route review</h2>
        <p className="mt-3 text-sm text-[#73798a]">No payments found.</p>
      </aside>
    );
  }

  const { payment, switch: sw } = entry;
  const offOptimal = entry.off_optimal_cents ?? 0;
  const fundingCard = compactCardName(payment.funding_card_name);
  const priorityCard = compactCardName(entry.priority_card_name);
  const independentCard = compactCardName(entry.independent_best_card_name);
  const statusTone =
    entry.priority_status === "optimal"
      ? "green"
      : entry.priority_status === "infeasible"
        ? "rose"
        : "amber";

  return (
    <aside className="panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold text-[#202332]">Route review</h2>
          <p className="mt-1 text-xs text-[#73798a]">{payment.name}</p>
        </div>
        <Badge tone={statusTone}>{entry.priority_status.replace("_", " ")}</Badge>
      </div>

      <div className="mt-4 rounded-[24px] border border-[#dde2eb] bg-white p-4 shadow-[0_18px_40px_-34px_rgba(41,47,70,0.46)]">
        <p className="text-xs font-medium text-[#73798a]">Recommended route</p>
        <div className="mt-4 space-y-3">
          <RouteStep label="Current" value={fundingCard} muted />
          <RouteStep label="Use" value={priorityCard} />
          <RouteStep label="Best alone" value={independentCard} muted={priorityCard === independentCard} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <ScoreTile label="Score" value={money(entry.weighted_priority_score_cents)} />
          <ScoreTile
            label="Impact"
            value={offOptimal > 0 ? `-${money(offOptimal)}` : "$0.00"}
            warn={offOptimal > 0}
          />
        </div>
      </div>

      <div className="mt-3 rounded-[20px] border border-[#dde2eb] bg-[#f7f8fb] p-3 text-xs leading-5 text-[#555d70]">
        {sw ? sw.headline : entry.priority_reason}
      </div>

      <Link href={`/payments/${payment.id}`} className="mt-3 inline-flex w-full items-center justify-center rounded-[18px] bg-[#465bd8] px-4 py-3 text-sm font-semibold text-white shadow-[0_16px_32px_-24px_rgba(70,91,216,0.85)] transition hover:bg-[#3849b7]">
        Open payment
      </Link>
    </aside>
  );
}

function PaymentRow({ entry }: { entry: DashboardPayment }) {
  const { payment, funding_eval, switch: sw, primary_card_name } = entry;

  return (
    <tr className="hover:bg-[#f8f9fc]">
      <td className="px-4 py-3">
        <Link href={`/payments/${payment.id}`} className="font-semibold text-[#202332] hover:text-[#465bd8]">
          {payment.name}
        </Link>
        <div className="mt-1 flex flex-wrap gap-1.5">
          <Badge tone="slate">{payment.category}</Badge>
          {sw ? <Badge tone="teal">switch</Badge> : <Badge tone="green">optimal</Badge>}
        </div>
      </td>
      <td className="px-4 py-3 text-[#73798a]">{payment.due_date}</td>
      <td className="tabular px-4 py-3 text-right font-semibold text-[#202332]">
        {money(payment.amount_cents)}
      </td>
      <td className="max-w-[190px] truncate px-4 py-3 text-[#73798a]">
        {payment.funding_card_name ?? "Unassigned"}
      </td>
      <td className="max-w-[190px] truncate px-4 py-3 font-medium text-[#465bd8]">
        {primary_card_name ?? "No eligible card"}
      </td>
      <td className="tabular px-4 py-3 text-right font-semibold">
        {funding_eval?.eligible ? (
          <span className="text-emerald-700">+{money(funding_eval.net_reward_cents)}</span>
        ) : (
          <span className="text-rose-700">Ineligible</span>
        )}
      </td>
    </tr>
  );
}

function AlertsPanel({ alerts }: { alerts: DashboardData["alerts"] }) {
  return (
    <div className="panel p-4">
      <h2 className="text-base font-semibold text-[#202332]">Alerts</h2>
      {alerts.length === 0 ? (
        <p className="mt-3 text-sm text-[#9aa1b2]">No alerts.</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {alerts.map((alert, index) => (
            <li key={index} className="flex items-start gap-2 text-sm leading-5 text-[#555d70]">
              <span className="mt-0.5">
                <Badge tone={ALERT_TONES[alert.kind] ?? "slate"}>
                  {alert.kind.replace("_", " ")}
                </Badge>
              </span>
              <span>
                {alert.message}
                {alert.transaction_id ? (
                  <>
                    {" "}
                    <Link href="/tracker" className="font-semibold text-[#465bd8] hover:underline">
                      Verify
                    </Link>
                  </>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CardsPanel({ cards }: { cards: DashboardData["cards"] }) {
  return (
    <div className="panel p-4">
      <h2 className="text-base font-semibold text-[#202332]">Card utilization</h2>
      <div className="mt-4 space-y-4">
        {cards.map((card) => {
          const bps =
            card.credit_limit_cents > 0
              ? Math.floor((card.current_balance_cents * 10000) / card.credit_limit_cents)
              : 10000;
          return (
            <div key={card.id}>
              <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
                <span className="min-w-0 truncate font-medium text-[#555d70]">{card.name}</span>
                <span className="shrink-0 text-[#73798a]">
                  {moneyShort(card.current_balance_cents)} / {moneyShort(card.credit_limit_cents)} ({pct(bps)})
                </span>
              </div>
              <ProgressBar value={bps / 100} tone={utilizationTone(bps)} />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  tone = "text-[#202332]",
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="soft-card px-4 py-4">
      <p className="text-xs font-medium text-[#73798a]">{label}</p>
      <p className={`tabular mt-1 text-[24px] font-semibold ${tone}`}>{value}</p>
    </div>
  );
}

function RouteStep({
  label,
  value,
  muted = false,
}: {
  label: string;
  value: string;
  muted?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-[#73798a]">{label}</span>
      <span className={`min-w-0 truncate text-right text-sm font-semibold ${muted ? "text-[#555d70]" : "text-[#202332]"}`}>
        {value}
      </span>
    </div>
  );
}

function ScoreTile({
  label,
  value,
  warn = false,
}: {
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="rounded-[16px] bg-[#f4f5f8] px-3 py-2">
      <p className="text-xs text-[#73798a]">{label}</p>
      <p className={`tabular mt-1 text-sm font-semibold ${warn ? "text-amber-700" : "text-[#202332]"}`}>
        {value}
      </p>
    </div>
  );
}

function compactCardName(name: string | null | undefined): string {
  if (!name) return "Unassigned";
  return name.replace(/\s*\(synthetic\)/gi, "").replace(/\s+synthetic/gi, "").trim();
}
