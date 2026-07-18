"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Card, CatalogProduct, api, fetchCatalog, money, moneyShort, pct } from "@/lib/api";
import {
  Badge,
  EmptyState,
  Modal,
  PageHeader,
  ProgressBar,
  utilizationTone,
} from "@/components/ui";

interface CardFormState {
  name: string;
  reward_type: "cashback" | "points";
  reward_rate: string; // percent for cashback, points-per-dollar for points
  point_value_millicents: string; // value of one point, in millicents (1000 = 1¢)
  credit_limit: string;
  current_balance: string;
  bonus_target: string;
  bonus_progress: string;
  bonus_value: string;
  bonus_deadline: string;
  expiry_date: string;
  status: "active" | "locked";
}

const EMPTY_FORM: CardFormState = {
  name: "",
  reward_type: "cashback",
  reward_rate: "1.5",
  point_value_millicents: "1000",
  credit_limit: "5000",
  current_balance: "0",
  bonus_target: "",
  bonus_progress: "",
  bonus_value: "",
  bonus_deadline: "",
  expiry_date: "",
  status: "active",
};

function toForm(card: Card): CardFormState {
  return {
    name: card.name,
    reward_type: card.reward_type,
    reward_rate: String(card.reward_rate_bps / 100),
    point_value_millicents: String(card.point_value_millicents || 1000),
    credit_limit: String(card.credit_limit_cents / 100),
    current_balance: String(card.current_balance_cents / 100),
    bonus_target: card.bonus_target_cents ? String(card.bonus_target_cents / 100) : "",
    bonus_progress: card.bonus_progress_cents ? String(card.bonus_progress_cents / 100) : "",
    bonus_value: card.bonus_value_cents ? String(card.bonus_value_cents / 100) : "",
    bonus_deadline: card.bonus_deadline ?? "",
    expiry_date: card.expiry_date ?? "",
    status: card.status,
  };
}

function toBody(form: CardFormState) {
  const cents = (v: string) => Math.round(parseFloat(v || "0") * 100);
  return {
    name: form.name,
    reward_type: form.reward_type,
    reward_rate_bps: Math.round(parseFloat(form.reward_rate || "0") * 100),
    point_value_millicents: Math.round(parseFloat(form.point_value_millicents || "1000")),
    credit_limit_cents: cents(form.credit_limit),
    current_balance_cents: cents(form.current_balance),
    bonus_target_cents: form.bonus_target ? cents(form.bonus_target) : null,
    bonus_progress_cents: form.bonus_progress ? cents(form.bonus_progress) : null,
    bonus_value_cents: form.bonus_value ? cents(form.bonus_value) : null,
    bonus_deadline: form.bonus_deadline || null,
    expiry_date: form.expiry_date || null,
    status: form.status,
    ineligible_categories: "",
  };
}

export default function CardsPage() {
  const [cards, setCards] = useState<Card[] | null>(null);
  const [editing, setEditing] = useState<Card | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<CardFormState>(EMPTY_FORM);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [catalog, setCatalog] = useState<CatalogProduct[]>([]);
  const [productId, setProductId] = useState("");

  const load = useCallback(async () => {
    setCards(await api<Card[]>("/cards"));
  }, []);

  useEffect(() => {
    load();
    // The sourced product catalog is optional — the blank form works without it.
    fetchCatalog().then(setCatalog).catch(() => setCatalog([]));
  }, [load]);

  const openAdd = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setProductId("");
    setError(null);
    setOpen(true);
  };

  const applyProduct = (id: string) => {
    setProductId(id);
    const product = catalog.find((p) => p.id === id);
    if (!product) return;
    setForm((f) => ({
      ...f,
      name: product.name,
      reward_type: product.base_reward_type === "cashback" ? "cashback" : "points",
      reward_rate: String(product.base_rate_bps / 100),
      point_value_millicents: String(product.point_value_millicents),
    }));
  };
  const openEdit = (card: Card) => {
    setEditing(card);
    setForm(toForm(card));
    setError(null);
    setOpen(true);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = JSON.stringify(toBody(form));
      if (editing) {
        await api(`/cards/${editing.id}`, { method: "PUT", body });
      } else {
        await api("/cards", { method: "POST", body });
      }
      setOpen(false);
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (card: Card) => {
    if (!confirm(`Remove ${card.name}?`)) return;
    await api(`/cards/${card.id}`, { method: "DELETE" });
    await load();
  };

  const set = (patch: Partial<CardFormState>) => setForm((f) => ({ ...f, ...patch }));
  const loadedCards = cards ?? [];
  const totalLimit = loadedCards.reduce((sum, card) => sum + card.credit_limit_cents, 0);
  const totalBalance = loadedCards.reduce((sum, card) => sum + card.current_balance_cents, 0);
  const totalAvailable = Math.max(0, totalLimit - totalBalance);
  const walletBps = totalLimit > 0 ? Math.floor((totalBalance * 10000) / totalLimit) : 0;
  const activeCount = loadedCards.filter((card) => card.status === "active").length;
  const bonusCards = loadedCards.filter((card) => card.bonus_target_cents && card.bonus_target_cents > 0);
  const remainingBonusSpend = bonusCards.reduce(
    (sum, card) =>
      sum + Math.max(0, (card.bonus_target_cents ?? 0) - (card.bonus_progress_cents ?? 0)),
    0,
  );

  return (
    <>
      <PageHeader
        title="Cards"
        subtitle="Credit limits, rewards, bonus progress, and card health in one wallet view."
        actions={
          <button className="btn-primary" onClick={openAdd}>
            Add card
          </button>
        }
      />

      {cards === null ? (
        <p className="text-sm text-slate-400">Loading cards…</p>
      ) : cards.length === 0 ? (
        <EmptyState message="No cards yet" hint="Add a synthetic card to start routing payments." />
      ) : (
        <div className="space-y-5">
          <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div className="panel overflow-hidden p-5">
              <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-center">
                <div>
                  <Badge tone="teal" dot>Wallet health</Badge>
                  <h2 className="mt-4 text-2xl font-semibold text-[#202332]">
                    {money(totalAvailable)} available
                  </h2>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-[#73798a]">
                    {activeCount} active card{activeCount === 1 ? "" : "s"} with{" "}
                    {money(totalBalance)} carried against {money(totalLimit)} in total limits.
                  </p>
                  <div className="mt-5 max-w-xl">
                    <div className="mb-2 flex items-center justify-between text-xs text-[#73798a]">
                      <span>Portfolio utilization</span>
                      <span>{pct(walletBps)}</span>
                    </div>
                    <ProgressBar value={walletBps / 100} tone={utilizationTone(walletBps)} />
                  </div>
                </div>

                <div className="rounded-[24px] border border-[#dde2eb] bg-[#f7f8fb] p-4">
                  <div className="relative mx-auto h-[190px] max-w-[330px]">
                    {cards.slice(0, 3).map((card, index) => (
                      <MiniWalletCard key={card.id} card={card} index={index} />
                    ))}
                  </div>
                </div>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
              <WalletMetric label="Cards" value={String(cards.length)} detail={`${activeCount} active`} />
              <WalletMetric
                label="Bonus left"
                value={moneyShort(remainingBonusSpend)}
                detail={`${bonusCards.length} bonus card${bonusCards.length === 1 ? "" : "s"}`}
              />
              <WalletMetric
                label="Failures"
                value={String(cards.reduce((sum, card) => sum + card.recent_failures, 0))}
                detail="recent charges"
              />
            </div>
          </section>

          <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
            {cards.map((card) => (
              <CreditCardTile
                key={card.id}
                card={card}
                onEdit={() => openEdit(card)}
                onRemove={() => remove(card)}
              />
            ))}
          </div>
        </div>
      )}

      <Modal
        title={editing ? `Edit ${editing.name}` : "Add synthetic card"}
        open={open}
        onClose={() => setOpen(false)}
      >
        <form onSubmit={submit} className="space-y-4">
          {!editing && catalog.length > 0 && (
            <div className="rounded-lg border border-indigo-200 bg-indigo-50/50 p-3">
              <label className="label">Start from a real product (optional)</label>
              <select
                className="field"
                value={productId}
                onChange={(e) => applyProduct(e.target.value)}
              >
                <option value="">Blank card — enter terms manually</option>
                {catalog.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} — {p.base_reward_type === "cashback" ? "cashback" : "points"},{" "}
                    ${(p.annual_fee_cents / 100).toFixed(0)} fee
                  </option>
                ))}
              </select>
              <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                Pre-fills the issuer&apos;s public base earn rate and point valuation from the
                sourced catalog. Category bonus rates aren&apos;t modeled in the wallet yet.
                Limits, balances, and welcome-bonus progress are yours to set — they stay
                synthetic.
              </p>
            </div>
          )}
          <div>
            <label className="label">Card name</label>
            <input
              className="field"
              required
              value={form.name}
              onChange={(e) => set({ name: e.target.value })}
              placeholder="e.g. Aurora Rewards (synthetic)"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Reward type</label>
              <select
                className="field"
                value={form.reward_type}
                onChange={(e) => set({ reward_type: e.target.value as "cashback" | "points" })}
              >
                <option value="cashback">Cashback</option>
                <option value="points">Points</option>
              </select>
              {form.reward_type === "points" && (
                <p className="mt-1 text-[11px] text-slate-500">
                  1 pt = {(parseFloat(form.point_value_millicents || "1000") / 1000).toFixed(2)}¢
                </p>
              )}
            </div>
            <div>
              <label className="label">
                {form.reward_type === "cashback" ? "Rate (%)" : "Points per $"}
              </label>
              <input
                className="field"
                type="number"
                step="0.1"
                min="0"
                required
                value={form.reward_rate}
                onChange={(e) => set({ reward_rate: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Credit limit ($)</label>
              <input
                className="field"
                type="number"
                min="0"
                required
                value={form.credit_limit}
                onChange={(e) => set({ credit_limit: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Current balance ($)</label>
              <input
                className="field"
                type="number"
                min="0"
                required
                value={form.current_balance}
                onChange={(e) => set({ current_balance: e.target.value })}
              />
            </div>
          </div>
          <fieldset className="rounded-lg border border-slate-200 p-3">
            <legend className="px-1 text-xs font-semibold text-slate-500">
              Welcome bonus (optional)
            </legend>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Spend target ($)</label>
                <input
                  className="field"
                  type="number"
                  min="0"
                  value={form.bonus_target}
                  onChange={(e) => set({ bonus_target: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Progress ($)</label>
                <input
                  className="field"
                  type="number"
                  min="0"
                  value={form.bonus_progress}
                  onChange={(e) => set({ bonus_progress: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Bonus value ($)</label>
                <input
                  className="field"
                  type="number"
                  min="0"
                  value={form.bonus_value}
                  onChange={(e) => set({ bonus_value: e.target.value })}
                />
              </div>
              <div>
                <label className="label">Deadline</label>
                <input
                  className="field"
                  type="date"
                  value={form.bonus_deadline}
                  onChange={(e) => set({ bonus_deadline: e.target.value })}
                />
              </div>
            </div>
          </fieldset>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">Expiry date</label>
              <input
                className="field"
                type="date"
                value={form.expiry_date}
                onChange={(e) => set({ expiry_date: e.target.value })}
              />
            </div>
            <div>
              <label className="label">Status</label>
              <select
                className="field"
                value={form.status}
                onChange={(e) => set({ status: e.target.value as "active" | "locked" })}
              >
                <option value="active">Active</option>
                <option value="locked">Locked</option>
              </select>
            </div>
          </div>
          {error && <p className="text-sm text-rose-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? "Saving…" : editing ? "Save changes" : "Add card"}
            </button>
          </div>
        </form>
      </Modal>
    </>
  );
}

function CreditCardTile({
  card,
  onEdit,
  onRemove,
}: {
  card: Card;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const bps =
    card.credit_limit_cents > 0
      ? Math.floor((card.current_balance_cents * 10000) / card.credit_limit_cents)
      : 10000;
  const bonusPct =
    card.bonus_target_cents && card.bonus_target_cents > 0
      ? ((card.bonus_progress_cents ?? 0) / card.bonus_target_cents) * 100
      : null;
  const art = cardArt(card.id);
  const available = Math.max(0, card.credit_limit_cents - card.current_balance_cents);

  return (
    <article className="panel panel-hover flex min-h-full flex-col overflow-hidden p-3">
      <div
        className={`relative aspect-[1.58/1] overflow-hidden rounded-[24px] ${art.surface} p-5 text-white`}
      >
        <div className={`pointer-events-none absolute inset-x-0 top-0 h-24 ${art.sheen}`} />
        <div className="relative flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold">{cleanCardName(card.name)}</p>
            <p className="mt-1 text-xs text-white/64">{rewardLabel(card)}</p>
          </div>
          <span className="rounded-full border border-white/20 bg-white/12 px-2.5 py-1 text-[11px] font-semibold text-white/85">
            {card.status}
          </span>
        </div>

        <div className="relative mt-7 flex items-center justify-between">
          <Chip />
          <NfcMark />
        </div>

        <p className="relative mt-5 font-mono text-[12px] tracking-[0.16em] text-white/78">
          {formatToken(card.token)}
        </p>

        <div className="relative mt-4 flex items-end justify-between gap-4 text-xs text-white/72">
          <div>
            <p className="text-[10px] uppercase text-white/45">Available</p>
            <p className="mt-0.5 text-sm font-semibold text-white">{moneyShort(available)}</p>
          </div>
          <div className="text-right">
            <p className="text-[10px] uppercase text-white/45">Expires</p>
            <p className="mt-0.5 font-semibold text-white">{formatExpiry(card.expiry_date)}</p>
          </div>
        </div>
      </div>

      <div className="flex flex-1 flex-col gap-4 p-2 pt-4">
        <div className="grid grid-cols-2 gap-2">
          <CardStat label="Limit" value={moneyShort(card.credit_limit_cents)} />
          <CardStat label="Balance" value={moneyShort(card.current_balance_cents)} />
        </div>

        <div>
          <div className="mb-2 flex justify-between text-xs text-[#73798a]">
            <span>Utilization</span>
            <span>
              {pct(bps)} · {moneyShort(card.current_balance_cents)} used
            </span>
          </div>
          <ProgressBar value={bps / 100} tone={utilizationTone(bps)} />
        </div>

        {bonusPct !== null ? (
          <div className="rounded-[18px] border border-[#dde2eb] bg-[#f7f8fb] p-3">
            <div className="mb-2 flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-[#202332]">Welcome bonus</p>
                <p className="mt-0.5 text-xs text-[#73798a]">
                  {money(card.bonus_value_cents ?? 0)} value · deadline {card.bonus_deadline ?? "open"}
                </p>
              </div>
              <Badge tone={bonusPct >= 100 ? "green" : "teal"}>
                {Math.min(100, Math.round(bonusPct))}%
              </Badge>
            </div>
            <ProgressBar value={bonusPct} tone="teal" />
            <p className="mt-2 text-xs text-[#73798a]">
              {moneyShort(card.bonus_progress_cents ?? 0)} of {moneyShort(card.bonus_target_cents ?? 0)}
            </p>
          </div>
        ) : (
          <div className="rounded-[18px] border border-dashed border-[#dde2eb] bg-white/55 p-3 text-xs text-[#73798a]">
            No welcome bonus attached.
          </div>
        )}

        {card.recent_failures > 0 ? (
          <div className="rounded-[16px] border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">
            {card.recent_failures} recent failed charge{card.recent_failures === 1 ? "" : "s"}
          </div>
        ) : null}

        <div className="mt-auto flex gap-2 pt-1">
          <button className="btn-secondary flex-1" onClick={onEdit}>
            Edit
          </button>
          <button className="btn-danger" onClick={onRemove}>
            Remove
          </button>
        </div>
      </div>
    </article>
  );
}

function MiniWalletCard({ card, index }: { card: Card; index: number }) {
  const art = cardArt(card.id);
  const positions = [
    "left-0 top-0 rotate-[-5deg]",
    "left-8 top-10 rotate-[2deg]",
    "left-16 top-20 rotate-[7deg]",
  ];

  return (
    <div
      className={`absolute h-[106px] w-[188px] rounded-[18px] ${art.surface} p-4 text-white shadow-[0_20px_42px_-28px_rgba(41,47,70,0.9)] ${positions[index]}`}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="truncate text-xs font-semibold">{cleanCardName(card.name)}</p>
        <span className="text-[10px] uppercase text-white/58">{card.reward_type}</span>
      </div>
      <div className="mt-5 flex items-center justify-between">
        <Chip compact />
        <span className="font-mono text-[10px] tracking-[0.14em] text-white/62">
          {formatToken(card.token).slice(-7)}
        </span>
      </div>
    </div>
  );
}

function WalletMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="soft-card px-4 py-4">
      <p className="text-xs font-medium text-[#73798a]">{label}</p>
      <p className="tabular mt-1 text-[24px] font-semibold text-[#202332]">{value}</p>
      <p className="mt-1 text-xs text-[#73798a]">{detail}</p>
    </div>
  );
}

function CardStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[16px] bg-[#f4f5f8] px-3 py-2">
      <p className="text-xs text-[#73798a]">{label}</p>
      <p className="tabular mt-1 text-sm font-semibold text-[#202332]">{value}</p>
    </div>
  );
}

function Chip({ compact = false }: { compact?: boolean }) {
  return (
    <span
      className={`relative block overflow-hidden rounded-[8px] bg-[linear-gradient(135deg,#f9df95,#b98b32)] ${
        compact ? "h-6 w-8" : "h-8 w-11"
      }`}
      aria-hidden
    >
      <span className="absolute inset-x-0 top-1/2 h-px bg-[#8f6823]/55" />
      <span className="absolute inset-y-0 left-1/2 w-px bg-[#8f6823]/55" />
      <span className="absolute left-1 top-1 h-2 w-2 rounded-sm border border-[#8f6823]/40" />
      <span className="absolute bottom-1 right-1 h-2 w-2 rounded-sm border border-[#8f6823]/40" />
    </span>
  );
}

function NfcMark() {
  return (
    <span
      className="flex h-8 w-8 items-center justify-center rounded-full border border-white/18 bg-white/8 text-white/72"
      aria-hidden
    >
      <svg viewBox="0 0 24 24" fill="none" className="h-5 w-5">
        <path
          d="M8 8.7a4.6 4.6 0 0 1 0 6.6M12 6a8.5 8.5 0 0 1 0 12M16 3.5a12.2 12.2 0 0 1 0 17"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

function cardArt(id: string) {
  const options = [
    {
      surface: "bg-[linear-gradient(135deg,#22283b_0%,#465bd8_100%)]",
      sheen: "bg-[linear-gradient(120deg,rgba(255,255,255,0.16),rgba(255,255,255,0))]",
    },
    {
      surface: "bg-[linear-gradient(135deg,#2d313a_0%,#8f5b3d_100%)]",
      sheen: "bg-[linear-gradient(120deg,rgba(255,235,204,0.18),rgba(255,255,255,0))]",
    },
    {
      surface: "bg-[linear-gradient(135deg,#1f2937_0%,#446069_100%)]",
      sheen: "bg-[linear-gradient(120deg,rgba(255,255,255,0.15),rgba(255,255,255,0))]",
    },
    {
      surface: "bg-[linear-gradient(135deg,#2b2535_0%,#6d4b88_100%)]",
      sheen: "bg-[linear-gradient(120deg,rgba(255,255,255,0.15),rgba(255,255,255,0))]",
    },
  ];
  return options[hashString(id) % options.length];
}

function hashString(value: string): number {
  return Math.abs(value.split("").reduce((sum, char) => sum + char.charCodeAt(0), 0));
}

function rewardLabel(card: Card): string {
  return card.reward_type === "cashback"
    ? `${(card.reward_rate_bps / 100).toFixed(1)}% cashback`
    : `${(card.reward_rate_bps / 100).toFixed(1)} pts / $`;
}

function cleanCardName(name: string): string {
  return name.replace(/\s*\(synthetic\)/gi, "").replace(/\s+synthetic/gi, "").trim();
}

function formatToken(token: string): string {
  return token.replace(/•/g, "*").replace(/\s+/g, " ");
}

function formatExpiry(expiry: string | null): string {
  if (!expiry) return "--/--";
  const [year, month] = expiry.split("-");
  return year && month ? `${month}/${year.slice(-2)}` : expiry;
}
