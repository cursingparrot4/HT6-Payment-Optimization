// Typed client for the SwitchPay API. All money values are integer cents.

export interface Card {
  id: string;
  name: string;
  token: string;
  reward_type: "cashback" | "points";
  reward_rate_bps: number;
  point_value_millicents: number;
  credit_limit_cents: number;
  current_balance_cents: number;
  bonus_target_cents: number | null;
  bonus_progress_cents: number | null;
  bonus_value_cents: number | null;
  bonus_deadline: string | null;
  expiry_date: string | null;
  status: "active" | "locked";
  ineligible_categories: string;
  recent_failures: number;
}

export interface Payment {
  id: string;
  name: string;
  category: string;
  amount_cents: number;
  due_date: string;
  frequency: string;
  processing_fee_bps: number;
  funding_card_id: string | null;
  funding_card_name?: string | null;
  backup_card_id: string | null;
  backup_card_name?: string | null;
  priority_rank: number;
  last_result: string | null;
}

export interface Evaluation {
  card_id: string;
  card_name: string;
  eligible: boolean;
  exclusion_reasons: string[];
  rank: number | null;
  reward_cents: number;
  fee_cents: number;
  net_reward_cents: number;
  bonus_score_cents: number;
  bonus_completes: boolean;
  bonus_remaining_before_cents: number;
  utilization_before_bps: number;
  utilization_after_bps: number;
  utilization_penalty_cents: number;
  risk_penalty_cents: number;
  failure_penalty_cents: number;
  available_credit_cents: number;
  score_cents: number;
}

export interface SwitchRec {
  from_card_id: string | null;
  from_card_name: string | null;
  to_card_id: string;
  to_card_name: string;
  delta_cents: number | null;
  headline: string;
  reasons: string[];
  risks: string[];
}

export interface Recommendation {
  payment_id: string;
  evaluated_on: string;
  ranked: Evaluation[];
  excluded: Evaluation[];
  primary_card_id: string | null;
  backup_card_id: string | null;
  winner_reasons: string[];
  rejected_reasons: Record<string, string>;
  change_conditions: string[];
  switch: SwitchRec | null;
  payment: Payment;
}

export interface TxnEvent {
  id: number;
  kind: string;
  from_state: string | null;
  to_state: string | null;
  message: string;
  created_at: string;
}

export interface Txn {
  id: string;
  payment_id: string;
  payment_name?: string;
  card_id: string;
  card_name: string;
  amount_cents: number;
  fee_cents: number;
  state: string;
  scenario: string;
  idempotency_key: string;
  failure_reason: string | null;
  created_at: string;
  updated_at: string;
  is_terminal: boolean;
  events?: TxnEvent[];
  duplicate?: boolean;
  needs_verification?: boolean;
  failover_recommendation?: Recommendation;
}

export interface DashboardData {
  payments: {
    payment: Payment;
    primary_card_id: string | null;
    primary_card_name: string | null;
    backup_card_id: string | null;
    funding_eval: Evaluation | null;
    switch: SwitchRec | null;
    priority_rank: number;
    priority_weight: number;
    priority_card_id: string | null;
    priority_card_name: string | null;
    priority_score_cents: number | null;
    weighted_priority_score_cents: number | null;
    independent_best_card_id: string | null;
    independent_best_card_name: string | null;
    independent_best_score_cents: number | null;
    optimal_priority_score_cents: number | null;
    off_optimal_cents: number | null;
    priority_status: "optimal" | "off_optimal" | "infeasible";
    priority_reason: string;
  }[];
  cards: Card[];
  alerts: { kind: string; message: string; payment_id?: string; transaction_id?: string }[];
  totals: {
    estimated_reward_cents: number;
    estimated_fee_cents: number;
    payment_count: number;
    card_count: number;
  };
}

export interface IntentWeights {
  max_cashback: number;
  max_travel: number;
  credit_health: number;
  hit_signup_bonus: number;
  max_cashflow: number;
  min_risk: number;
}

export interface ParsedIntent {
  weights: IntentWeights;
  constraints: {
    max_utilization_bps: number | null;
    max_utilization_until: string | null;
    must_hit_bonus_card_ids: string[];
  };
}

export interface ParseIntentResult {
  intent: ParsedIntent | null;
  source: "freesolo" | "gemini" | "prompted" | "fixture" | "fallback";
  provider_name: string | null;
  model_id: string | null;
  used_fallback: boolean;
  valid_model_output: boolean;
  warnings: { code: string; message: string }[];
  raw_output_available: boolean;
}

export type IntentProviderName = "auto" | "freesolo" | "gemini" | "fixture";

export const savePaymentPriorities = (paymentIds: string[]): Promise<Payment[]> =>
  api<Payment[]>("/payment-priorities", {
    method: "PUT",
    body: JSON.stringify({ payment_ids: paymentIds }),
  });

export const parseIntent = async (body: {
  text: string;
  reference_date: string;
  card_context: { id: string; name: string; has_active_bonus: boolean }[];
  allow_fallback?: boolean;
  provider?: IntentProviderName;
}): Promise<ParseIntentResult> => {
  const res = await api<{ data: { result: ParseIntentResult } }>("/parse-intent", {
    method: "POST",
    body: JSON.stringify(body),
  });
  return res.data.result;
};

// Public issuer product terms from data/cards.json — no account data.
export interface CatalogProduct {
  id: string;
  name: string;
  issuer: string;
  network: string;
  reward_program: string;
  annual_fee_cents: number;
  reward_rules: { category: string; rate_bps: number; reward_type: string }[];
  base_rate_bps: number;
  base_reward_type: "cashback" | "points" | "miles";
  point_value_millicents: number;
  point_value_basis: string;
}

export const fetchCatalog = async (): Promise<CatalogProduct[]> => {
  const res = await api<{ data: { catalog: { products: CatalogProduct[] } } }>("/catalog");
  return res.data.catalog.products;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    cache: "no-store",
    headers: { "content-type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? JSON.stringify(body);
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export const money = (cents: number | null | undefined): string => {
  if (cents === null || cents === undefined) return "—";
  const sign = cents < 0 ? "-" : "";
  return `${sign}$${(Math.abs(cents) / 100).toLocaleString("en-CA", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
};

export const moneyShort = (cents: number | null | undefined): string => {
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toLocaleString("en-CA", { maximumFractionDigits: 0 })}`;
};

export const pct = (bps: number): string => `${(bps / 100).toFixed(1)}%`;

export const newIdempotencyKey = (): string =>
  `switchpay-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

export const STATE_LABELS: Record<string, string> = {
  scheduled: "Scheduled",
  authorization_pending: "Card authorization pending",
  authorized: "Authorized",
  processing: "Processing",
  recipient_paid: "Recipient paid",
  reconciled: "Reconciled",
  failed: "Failed",
  status_uncertain: "Status uncertain",
};

export const SCENARIO_LABELS: Record<string, string> = {
  success: "Successful payment",
  card_declined: "Card decline",
  insufficient_credit: "Insufficient available credit",
  card_locked: "Card locked",
  card_expired: "Expired card",
  network_timeout: "Network timeout",
  unknown_auth: "Unknown authorization status",
};
