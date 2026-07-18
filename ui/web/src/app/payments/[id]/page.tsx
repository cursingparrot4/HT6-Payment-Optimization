"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { Evaluation, Recommendation, api, money, pct } from "@/lib/api";
import { Badge, PageHeader, ProgressBar, utilizationTone } from "@/components/ui";

export default function RecommendationPage() {
  const params = useParams<{ id: string }>();
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);

  const load = useCallback(async () => {
    try {
      setRec(await api<Recommendation>(`/payments/${params.id}/recommendation`));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [params.id]);

  useEffect(() => {
    load();
  }, [load]);

  const approveSwitch = async () => {
    if (!rec?.switch) return;
    setApproving(true);
    try {
      await api(`/payments/${params.id}/approve-switch`, {
        method: "POST",
        body: JSON.stringify({ to_card_id: rec.switch.to_card_id }),
      });
      setApproved(true);
      await load();
      setTimeout(() => setApproved(false), 4000);
    } finally {
      setApproving(false);
    }
  };

  if (error) return <div className="panel p-8 text-sm text-rose-700">{error}</div>;
  if (!rec) return <p className="text-sm text-slate-400">Evaluating cards…</p>;

  const { payment } = rec;
  const primary = rec.ranked.find((e) => e.card_id === rec.primary_card_id) ?? null;
  const backup = rec.ranked.find((e) => e.card_id === rec.backup_card_id) ?? null;

  return (
    <>
      <PageHeader
        title={`${payment.name}: card recommendation`}
        subtitle={`${money(payment.amount_cents)} · ${payment.frequency} · due ${payment.due_date} · evaluated deterministically on ${rec.evaluated_on}. No AI model performs any arithmetic or makes the final decision.`}
        actions={
          <Link href="/tracker" className="btn-secondary">
            Go to tracker
          </Link>
        }
      />

      {approved && (
        <div className="panel mb-5 border-emerald-200 bg-emerald-50 p-4 text-sm font-medium text-emerald-800">
          Switch approved. {payment.name} is now funded by {payment.funding_card_name}.
        </div>
      )}

      {rec.switch ? (
        <div className="animate-fade-up-delay-1 panel mb-5 border-teal-200 bg-teal-50/45 p-5">
          <div className="mb-2 flex items-center gap-2">
            <Badge tone="teal" dot>Switch recommendation</Badge>
            {rec.switch.delta_cents !== null && (
              <Badge tone="green">+{money(rec.switch.delta_cents)} / cycle</Badge>
            )}
          </div>
          <p className="mb-4 max-w-4xl text-[18px] font-semibold leading-7 tracking-tight text-slate-950">
            {rec.switch.headline}
          </p>
          <div className="mb-4 grid gap-4 md:grid-cols-2">
            <div>
              <p className="section-title mb-1.5">
                Why switch
              </p>
              <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700">
                {rec.switch.reasons.map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            </div>
            <div>
              <p className="section-title mb-1.5">
                Risks created by switching
              </p>
              <ul className="list-disc space-y-1 pl-4 text-sm text-slate-700">
                {rec.switch.risks.map((risk, i) => (
                  <li key={i}>{risk}</li>
                ))}
              </ul>
            </div>
          </div>
          <button className="btn-primary" onClick={approveSwitch} disabled={approving}>
            {approving
              ? "Approving…"
              : `Approve switch to ${rec.switch.to_card_name}`}
          </button>
          <p className="mt-2 text-xs text-slate-400">
            Nothing changes until you approve. SwitchPay never switches cards silently.
          </p>
        </div>
      ) : (
        <div className="panel mb-5 border-emerald-200 bg-emerald-50/60 p-4 text-sm text-emerald-800">
          <span className="font-semibold">No switch needed.</span> The current funding card (
          {payment.funding_card_name ?? "unassigned"}) is already the top-ranked card for this
          payment.
        </div>
      )}

      <div className="mb-5 grid gap-4 lg:grid-cols-2">
        {primary && <RoleCard role="Primary card" tone="teal" evaluation={primary} />}
        {backup ? (
          <RoleCard role="Backup card" tone="sky" evaluation={backup} />
        ) : (
          <div className="panel flex items-center justify-center p-5 text-sm text-slate-400">
            No eligible backup card. Add another card to protect this payment.
          </div>
        )}
      </div>

      <div className="panel mb-5 p-4">
        <h2 className="section-title mb-3">
          Full ranking &amp; scoring breakdown
        </h2>
        <div className="space-y-4">
          {rec.ranked.map((evaluation) => (
            <EvaluationRow
              key={evaluation.card_id}
              evaluation={evaluation}
              isPrimary={evaluation.card_id === rec.primary_card_id}
              isBackup={evaluation.card_id === rec.backup_card_id}
              isCurrent={evaluation.card_id === payment.funding_card_id}
              lossReason={rec.rejected_reasons[evaluation.card_id]}
            />
          ))}
          {rec.excluded.map((evaluation) => (
            <div
              key={evaluation.card_id}
              className="rounded-lg border border-rose-200 bg-rose-50/50 p-4"
            >
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-700">{evaluation.card_name}</span>
                <Badge tone="rose">excluded</Badge>
                {evaluation.card_id === payment.funding_card_id && (
                  <Badge tone="amber">current funding card</Badge>
                )}
              </div>
              <ul className="mt-1 list-disc pl-5 text-sm text-rose-700">
                {evaluation.exclusion_reasons.map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="panel p-4">
          <h2 className="section-title mb-3">
            Why {primary?.card_name ?? "the winner"} won
          </h2>
          <ul className="list-disc space-y-2 pl-4 text-sm text-slate-700">
            {rec.winner_reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
        <div className="panel p-4">
          <h2 className="section-title mb-3">
            What would change this recommendation
          </h2>
          {rec.change_conditions.length === 0 ? (
            <p className="text-sm text-slate-400">
              This ranking is stable under the current card states.
            </p>
          ) : (
            <ul className="list-disc space-y-2 pl-4 text-sm text-slate-700">
              {rec.change_conditions.map((condition, i) => (
                <li key={i}>{condition}</li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </>
  );
}

function RoleCard({
  role,
  tone,
  evaluation,
}: {
  role: string;
  tone: "teal" | "sky";
  evaluation: Evaluation;
}) {
  const border = tone === "teal" ? "border-teal-300" : "border-sky-200";
  return (
    <div className={`panel ${border} p-4`}>
      <div className="mb-2 flex items-center justify-between">
        <Badge tone={tone}>{role}</Badge>
        <span className="text-sm font-bold text-slate-900">
          score {money(evaluation.score_cents)}
        </span>
      </div>
      <p className="text-lg font-semibold text-slate-900">{evaluation.card_name}</p>
      <p className="mt-1 text-xs text-slate-500">
        {money(evaluation.available_credit_cents)} available credit · utilization{" "}
        {pct(evaluation.utilization_before_bps)} to {pct(evaluation.utilization_after_bps)} after
        payment
      </p>
      {evaluation.bonus_completes && (
        <p className="mt-2 text-sm font-medium text-emerald-700">
          This payment completes the welcome bonus (+{money(evaluation.bonus_score_cents)}).
        </p>
      )}
    </div>
  );
}

function EvaluationRow({
  evaluation,
  isPrimary,
  isBackup,
  isCurrent,
  lossReason,
}: {
  evaluation: Evaluation;
  isPrimary: boolean;
  isBackup: boolean;
  isCurrent: boolean;
  lossReason?: string;
}) {
  const [openDetail, setOpenDetail] = useState(isPrimary);
  return (
    <div
      className={`rounded-lg border p-4 ${
        isPrimary ? "border-teal-300 bg-teal-50/40" : "border-slate-200"
      }`}
    >
      <button
        className="flex w-full items-center justify-between gap-3 text-left"
        onClick={() => setOpenDetail((v) => !v)}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
            {evaluation.rank}
          </span>
          <span className="font-semibold text-slate-900">{evaluation.card_name}</span>
          {isPrimary && <Badge tone="teal">primary</Badge>}
          {isBackup && <Badge tone="sky">backup</Badge>}
          {isCurrent && <Badge tone="amber">current funding card</Badge>}
          {evaluation.bonus_completes && <Badge tone="green">completes bonus</Badge>}
        </div>
        <div className="flex items-center gap-3">
          <span className="font-bold text-slate-900">{money(evaluation.score_cents)}</span>
          <span className="text-slate-400">{openDetail ? "▴" : "▾"}</span>
        </div>
      </button>

      {!isPrimary && lossReason && (
        <p className="mt-2 text-xs text-slate-500">{lossReason}</p>
      )}

      {openDetail && (
        <div className="mt-4 grid gap-x-8 gap-y-2 border-t border-slate-200/70 pt-4 text-sm sm:grid-cols-2">
          <BreakdownLine label="Base reward value" value={evaluation.reward_cents} positive />
          <BreakdownLine label="Processing fee" value={-evaluation.fee_cents} />
          <BreakdownLine label="Net reward value" value={evaluation.net_reward_cents} positive bold />
          <BreakdownLine
            label={evaluation.bonus_completes ? "Welcome bonus (completed!)" : "Welcome bonus progress"}
            value={evaluation.bonus_score_cents}
            positive
          />
          <BreakdownLine label="Utilization penalty" value={-evaluation.utilization_penalty_cents} />
          <BreakdownLine label="Repayment-risk penalty" value={-evaluation.risk_penalty_cents} />
          <BreakdownLine label="Failure-history penalty" value={-evaluation.failure_penalty_cents} />
          <BreakdownLine label="Total score" value={evaluation.score_cents} positive bold />
          <div className="sm:col-span-2">
            <div className="mb-1 mt-2 flex justify-between text-xs text-slate-500">
              <span>
                Utilization after payment: {pct(evaluation.utilization_after_bps)}
              </span>
              <span>{money(evaluation.available_credit_cents)} available</span>
            </div>
            <ProgressBar
              value={evaluation.utilization_after_bps / 100}
              tone={utilizationTone(evaluation.utilization_after_bps)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function BreakdownLine({
  label,
  value,
  positive = false,
  bold = false,
}: {
  label: string;
  value: number;
  positive?: boolean;
  bold?: boolean;
}) {
  const color =
    value === 0 ? "text-slate-400" : value > 0 && positive ? "text-emerald-600" : value < 0 ? "text-rose-600" : "text-slate-700";
  return (
    <div className={`flex items-center justify-between ${bold ? "font-bold" : ""}`}>
      <span className="text-slate-600">{label}</span>
      <span className={color}>
        {value > 0 && positive ? "+" : ""}
        {money(value)}
      </span>
    </div>
  );
}
