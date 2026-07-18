"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Card, Payment, api, money } from "@/lib/api";
import { Badge, EmptyState, Modal, PageHeader } from "@/components/ui";

const CATEGORIES = [
  "rent",
  "recurring",
  "utilities",
  "insurance",
  "transit",
  "streaming",
  "tuition",
  "taxes",
  "other",
];

interface PaymentFormState {
  name: string;
  category: string;
  amount: string;
  due_date: string;
  frequency: string;
  processing_fee_pct: string;
  funding_card_id: string;
}

const EMPTY_FORM: PaymentFormState = {
  name: "",
  category: "rent",
  amount: "2400",
  due_date: "",
  frequency: "monthly",
  processing_fee_pct: "0",
  funding_card_id: "",
};

export default function PaymentsPage() {
  const [payments, setPayments] = useState<Payment[] | null>(null);
  const [cards, setCards] = useState<Card[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Payment | null>(null);
  const [form, setForm] = useState<PaymentFormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    const [p, c] = await Promise.all([api<Payment[]>("/payments"), api<Card[]>("/cards")]);
    setPayments(p);
    setCards(c);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const openAdd = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setError(null);
    setOpen(true);
  };

  const openEdit = (payment: Payment) => {
    setEditing(payment);
    setForm({
      name: payment.name,
      category: payment.category,
      amount: String(payment.amount_cents / 100),
      due_date: payment.due_date,
      frequency: payment.frequency,
      processing_fee_pct: String(payment.processing_fee_bps / 100),
      funding_card_id: payment.funding_card_id ?? "",
    });
    setError(null);
    setOpen(true);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = JSON.stringify({
        name: form.name,
        category: form.category,
        amount_cents: Math.round(parseFloat(form.amount) * 100),
        due_date: form.due_date,
        frequency: form.frequency,
        processing_fee_bps: Math.round(parseFloat(form.processing_fee_pct || "0") * 100),
        funding_card_id: form.funding_card_id || null,
      });
      if (editing) {
        await api(`/payments/${editing.id}`, { method: "PUT", body });
      } else {
        await api("/payments", { method: "POST", body });
      }
      setOpen(false);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (payment: Payment) => {
    if (!confirm(`Remove ${payment.name}?`)) return;
    await api(`/payments/${payment.id}`, { method: "DELETE" });
    await load();
  };

  const set = (patch: Partial<PaymentFormState>) => setForm((f) => ({ ...f, ...patch }));

  return (
    <>
      <PageHeader
        title="Recurring payments"
        subtitle="Large synthetic payments SwitchPay routes to the best card before every due date."
        actions={
          <button className="btn-primary" onClick={openAdd}>
            Add payment
          </button>
        }
      />

      {payments === null ? (
        <p className="text-sm text-slate-400">Loading payments…</p>
      ) : payments.length === 0 ? (
        <EmptyState message="No recurring payments" hint="Add rent, tuition, or another large bill." />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200 text-left text-[11px] font-semibold uppercase tracking-[0.1em] text-slate-400">
                <th className="px-5 py-3">Payment</th>
                <th className="px-5 py-3">Amount</th>
                <th className="px-5 py-3">Due</th>
                <th className="px-5 py-3">Frequency</th>
                <th className="px-5 py-3">Fee</th>
                <th className="px-5 py-3">Funding card</th>
                <th className="px-5 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {payments.map((payment) => (
                <tr key={payment.id} className="hover:bg-slate-50">
                  <td className="px-5 py-4">
                    <Link
                      href={`/payments/${payment.id}`}
                      className="font-semibold text-slate-900 hover:text-teal-700"
                    >
                      {payment.name}
                    </Link>
                    <div className="mt-0.5">
                      <Badge tone="slate">{payment.category}</Badge>
                    </div>
                  </td>
                  <td className="px-5 py-4 font-semibold">{money(payment.amount_cents)}</td>
                  <td className="px-5 py-4 text-slate-600">{payment.due_date}</td>
                  <td className="px-5 py-4 text-slate-600">{payment.frequency}</td>
                  <td className="px-5 py-4 text-slate-600">
                    {(payment.processing_fee_bps / 100).toFixed(1)}%
                  </td>
                  <td className="px-5 py-4 text-slate-600">
                    {payment.funding_card_name ?? <span className="text-rose-500">unassigned</span>}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex justify-end gap-2">
                      <Link href={`/payments/${payment.id}`} className="btn-primary !px-3 !py-1.5">
                        Recommendation
                      </Link>
                      <button className="btn-secondary !px-3 !py-1.5" onClick={() => openEdit(payment)}>
                        Edit
                      </button>
                      <button className="btn-danger !px-3 !py-1.5" onClick={() => remove(payment)}>
                        Remove
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        title={editing ? `Edit ${editing.name}` : "Add recurring payment"}
        open={open}
        onClose={() => setOpen(false)}
      >
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="label">Payment name</label>
            <input
              className="field"
              required
              value={form.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="e.g. Rent"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Category</label>
              <select
                className="field"
                value={form.category}
                onChange={(e) => set({ category: e.target.value })}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Amount ($)</label>
              <input
                className="field"
                type="number"
                min="1"
                step="0.01"
                required
                value={form.amount}
                onChange={(e) => set({ amount: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Next due date</label>
              <input
                className="field"
                type="date"
                required
                value={form.due_date}
                onChange={(e) => set({ due_date: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Frequency</label>
              <select
                className="field"
                value={form.frequency}
                onChange={(e) => set({ frequency: e.target.value })}
              >
                {["monthly", "weekly", "biweekly", "yearly", "once"].map((f) => (
                  <option key={f} value={f}>
                    {f}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">Processing fee (%)</label>
              <input
                className="field"
                type="number"
                min="0"
                step="0.1"
                value={form.processing_fee_pct}
                onChange={(e) => set({ processing_fee_pct: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Funding card</label>
              <select
                className="field"
                value={form.funding_card_id}
                onChange={(e) => set({ funding_card_id: e.target.value })}
              >
                <option value="">— unassigned</option>
                {cards.map((card) => (
                  <option key={card.id} value={card.id}>
                    {card.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : editing ? "Save changes" : "Add payment"}
            </button>
          </div>
        </form>
      </Modal>
    </>
  );
}
