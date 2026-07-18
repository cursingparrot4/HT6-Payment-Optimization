"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Payment,
  SCENARIO_LABELS,
  STATE_LABELS,
  Txn,
  api,
  money,
  newIdempotencyKey,
} from "@/lib/api";
import { Badge, PageHeader } from "@/components/ui";

const HAPPY_PATH = [
  "scheduled",
  "authorization_pending",
  "authorized",
  "processing",
  "recipient_paid",
  "reconciled",
];

const INTERNAL_SCENARIO_LABELS: Record<string, string> = {
  verified_success: "Verified — original charge found",
  verified_failed: "Verified — no charge found",
};

const STATE_TONES: Record<string, "slate" | "green" | "amber" | "rose" | "teal" | "sky"> = {
  scheduled: "slate",
  authorization_pending: "amber",
  authorized: "teal",
  processing: "teal",
  recipient_paid: "green",
  reconciled: "green",
  failed: "rose",
  status_uncertain: "sky",
};

export default function TrackerPage() {
  const [payments, setPayments] = useState<Payment[]>([]);
  const [txns, setTxns] = useState<Txn[]>([]);
  const [selectedPayment, setSelectedPayment] = useState("");
  const [scenario, setScenario] = useState("success");
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failovers, setFailovers] = useState<Record<string, Txn["failover_recommendation"]>>({});
  const advancing = useRef<Set<string>>(new Set());

  const load = useCallback(async () => {
    const [p, t] = await Promise.all([api<Payment[]>("/payments"), api<Txn[]>("/transactions")]);
    setPayments(p);
    setTxns(t);
    if (p.length > 0) {
      setSelectedPayment((cur) => cur || p[0].id);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const showToast = (message: string) => {
    setToast(message);
    setTimeout(() => setToast(null), 6000);
  };

  const autoAdvance = useCallback(
    async (txnId: string) => {
      if (advancing.current.has(txnId)) return;
      advancing.current.add(txnId);
      try {
        for (let i = 0; i < 8; i++) {
          await new Promise((resolve) => setTimeout(resolve, 900));
          const txn = await api<Txn>(`/transactions/${txnId}/advance`, { method: "POST" });
          setTxns((prev) => {
            const next = prev.filter((t) => t.id !== txn.id);
            return [{ ...txn, payment_name: prev.find((t) => t.id === txn.id)?.payment_name ?? txn.payment_name }, ...next];
          });
          if (txn.failover_recommendation) {
            setFailovers((prev) => ({ ...prev, [txn.id]: txn.failover_recommendation }));
            showToast(
              "Primary card failed. SwitchPay reran card selection and recommends the backup card — no duplicate charge was created."
            );
          }
          if (txn.is_terminal || txn.needs_verification) break;
        }
      } finally {
        advancing.current.delete(txnId);
        await load();
      }
    },
    [load]
  );

  const startPayment = async (duplicateDemo: boolean) => {
    if (!selectedPayment) return;
    setBusy(true);
    try {
      const key = newIdempotencyKey();
      const first = await api<Txn>(`/payments/${selectedPayment}/pay`, {
        method: "POST",
        body: JSON.stringify({ idempotency_key: key, scenario }),
      });
      if (duplicateDemo) {
        const second = await api<Txn>(`/payments/${selectedPayment}/pay`, {
          method: "POST",
          body: JSON.stringify({ idempotency_key: key, scenario }),
        });
        if (second.duplicate) {
          showToast(
            `Duplicate click blocked: both requests carried idempotency key “${key.slice(
              0,
              24
            )}…”, so only one synthetic transaction exists.`
          );
        }
      }
      await load();
      autoAdvance(first.id);
    } catch (e) {
      showToast(`Error: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const verify = async (txnId: string, confirmed: boolean) => {
    const txn = await api<Txn>(`/transactions/${txnId}/verify`, {
      method: "POST",
      body: JSON.stringify({ confirmed }),
    });
    await load();
    if (!txn.is_terminal) autoAdvance(txnId);
  };

  return (
    <>
      <PageHeader
        title="Payment tracker"
        subtitle="Simulate payments through the full state machine — including declines, timeouts, and uncertain authorizations — with idempotency protection throughout."
      />

      {toast && (
        <div className="animate-toast-in fixed bottom-6 right-6 z-50 max-w-md rounded-lg border border-slate-800 bg-slate-950 p-4 text-sm font-medium leading-relaxed text-white shadow-lg">
          {toast}
        </div>
      )}

      <div className="panel mb-8 p-6">
        <h2 className="section-title mb-4">
          Payment simulator
        </h2>
        <div className="flex flex-wrap items-end gap-4">
          <div className="min-w-56">
            <label className="label">Payment</label>
            <select
              className="field"
              value={selectedPayment}
              onChange={(e) => setSelectedPayment(e.target.value)}
            >
              {payments.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} — {money(p.amount_cents)} on {p.funding_card_name ?? "unassigned"}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-56">
            <label className="label">Simulated outcome</label>
            <select className="field" value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {Object.entries(SCENARIO_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          <button className="btn-primary" disabled={busy || !selectedPayment} onClick={() => startPayment(false)}>
            {busy ? "Submitting…" : "Run payment"}
          </button>
          <button
            className="btn-secondary"
            disabled={busy || !selectedPayment}
            onClick={() => startPayment(true)}
            title="Sends the same request twice with one idempotency key"
          >
            Simulate double-click
          </button>
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Every request carries an idempotency key — pressing pay twice can never create two
          synthetic transactions. The card&apos;s real state (locked, expired, over-limit)
          overrides an optimistic scenario.
        </p>
      </div>

      <h2 className="section-title mb-4">
        Transactions
      </h2>
      {txns.length === 0 ? (
        <div className="panel p-10 text-center text-sm text-slate-400">
          No transactions yet — run a payment above.
        </div>
      ) : (
        <div className="space-y-4">
          {txns.map((txn) => (
            <TxnCard
              key={txn.id}
              txn={{ ...txn, failover_recommendation: txn.failover_recommendation ?? failovers[txn.id] }}
              onVerify={verify}
            />
          ))}
        </div>
      )}
    </>
  );
}

const STEP_SHORT: Record<string, string> = {
  scheduled: "Scheduled",
  authorization_pending: "Auth pending",
  authorized: "Authorized",
  processing: "Processing",
  recipient_paid: "Paid",
  reconciled: "Reconciled",
};

function Stepper({ txn, stepIndex }: { txn: Txn; stepIndex: number }) {
  const failed = txn.state === "failed";
  const uncertain = txn.state === "status_uncertain";
  // Failures and uncertainty branch off during authorization.
  const reachedIndex = failed || uncertain ? 1 : stepIndex;
  return (
    <div className="overflow-x-auto pb-1">
      <div className="flex min-w-[540px] items-start">
        {HAPPY_PATH.map((state, i) => {
          const reached = i <= reachedIndex;
          const isEdge = i === reachedIndex && !txn.is_terminal;
          const done = reached && !((failed || uncertain) && i === reachedIndex);
          let dotClass = "border-slate-200 bg-white text-slate-300";
          let icon: React.ReactNode = (
            <span className="h-1.5 w-1.5 rounded-full bg-current" />
          );
          if (done) {
            dotClass = "border-emerald-500 bg-emerald-500 text-white";
            icon = (
              <svg viewBox="0 0 12 12" fill="none" className="h-3 w-3">
                <path d="M2.5 6.5 5 9l4.5-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            );
          }
          if (failed && i === reachedIndex) {
            dotClass = "border-rose-500 bg-rose-500 text-white";
            icon = (
              <svg viewBox="0 0 12 12" fill="none" className="h-3 w-3">
                <path d="M3 3l6 6M9 3 3 9" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            );
          }
          if (uncertain && i === reachedIndex) {
            dotClass = "border-sky-500 bg-sky-500 text-white";
            icon = <span className="text-[9px] font-bold leading-none">?</span>;
          }
          if (!failed && !uncertain && isEdge && i > 0) {
            dotClass = "animate-pulse-dot border-teal-600 bg-teal-600 text-white";
          }
          return (
            <div key={state} className="flex flex-1 flex-col items-center">
              <div className="flex w-full items-center">
                <div
                  className={`h-px flex-1 ${
                    i === 0 ? "invisible" : i <= reachedIndex ? "bg-emerald-400/70" : "bg-slate-200"
                  } ${(failed || uncertain) && i === reachedIndex ? (failed ? "!bg-rose-300" : "!bg-sky-300") : ""}`}
                />
                <div
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${dotClass}`}
                >
                  {icon}
                </div>
                <div
                  className={`h-px flex-1 ${
                    i === HAPPY_PATH.length - 1
                      ? "invisible"
                      : i < reachedIndex
                        ? "bg-emerald-400/70"
                        : "bg-slate-200"
                  }`}
                />
              </div>
              <span className="mt-1.5 text-[9.5px] font-semibold uppercase tracking-wide text-slate-400">
                {STEP_SHORT[state]}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TxnCard({
  txn,
  onVerify,
}: {
  txn: Txn;
  onVerify: (id: string, confirmed: boolean) => void;
}) {
  const stepIndex = HAPPY_PATH.indexOf(txn.state);
  const failover = txn.failover_recommendation;
  return (
    <div className="panel p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-900">{txn.payment_name}</span>
            <Badge tone={STATE_TONES[txn.state] ?? "slate"}>{STATE_LABELS[txn.state]}</Badge>
            <Badge tone="slate">
              {SCENARIO_LABELS[txn.scenario] ??
                INTERNAL_SCENARIO_LABELS[txn.scenario] ??
                txn.scenario}
            </Badge>
          </div>
          <p className="mt-0.5 text-xs text-slate-500">
            {money(txn.amount_cents)} {txn.fee_cents > 0 ? `(+${money(txn.fee_cents)} fee)` : ""} on{" "}
            {txn.card_name} · key{" "}
            <span className="font-mono">{txn.idempotency_key.slice(0, 26)}…</span>
          </p>
        </div>
        <span className="text-xs text-slate-400">{txn.updated_at.replace("T", " ").slice(0, 19)}</span>
      </div>

      {/* Progress stepper */}
      <Stepper txn={txn} stepIndex={stepIndex} />

      {txn.state === "status_uncertain" && (
        <div className="mt-4 rounded-lg border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900">
          <p className="font-semibold">
            Payment status is uncertain. SwitchPay will verify the original transaction before
            attempting another charge.
          </p>
          <p className="mt-1 text-xs text-sky-700">
            No automatic retry occurs — retrying blindly could double-charge the card.
          </p>
          <div className="mt-3 flex gap-2">
            <button className="btn-primary !py-1.5" onClick={() => onVerify(txn.id, true)}>
              Verify: original charge found
            </button>
            <button className="btn-secondary !py-1.5" onClick={() => onVerify(txn.id, false)}>
              Verify: no charge found
            </button>
          </div>
        </div>
      )}

      {txn.state === "failed" && txn.failure_reason && (
        <div className="mt-4 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <p className="font-semibold">Payment failed</p>
          <p className="mt-0.5">{txn.failure_reason}</p>
        </div>
      )}

      {failover && failover.primary_card_id && (
        <div className="mt-3 rounded-lg border border-teal-200 bg-teal-50 p-4 text-sm text-teal-900">
          <p className="font-semibold">
            Backup recommended: {failover.ranked[0]?.card_name}
          </p>
          <p className="mt-0.5 text-xs">
            SwitchPay reran the card-selection algorithm excluding the failed card. No duplicate
            payment was created — approve the switch, then retry with a fresh idempotency key.
          </p>
          <Link
            href={`/payments/${txn.payment_id}`}
            className="btn-primary mt-2 inline-flex !py-1.5"
          >
            Review backup recommendation
          </Link>
        </div>
      )}
    </div>
  );
}
