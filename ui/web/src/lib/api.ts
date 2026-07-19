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

export const savePaymentPriorities = (paymentIds: string[]): Promise<Payment[]> =>
  api<Payment[]>("/payment-priorities", {
    method: "PUT",
    body: JSON.stringify({ payment_ids: paymentIds }),
  });

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

// ---------------------------------------------------------------------------
// Deterministic optimization engine (PLAN.md §11). These endpoints return the
// weighted multi-objective plan plus faithful, templated explanations — the
// money math the simpler SwitchPay ranking above does not perform.
// ---------------------------------------------------------------------------

export type GoalKey =
  | "max_cashback"
  | "max_travel"
  | "credit_health"
  | "hit_signup_bonus"
  | "max_cashflow"
  | "min_risk";

export const GOAL_LABELS: Record<GoalKey, string> = {
  max_cashback: "Cashback",
  max_travel: "Travel value",
  credit_health: "Credit health",
  hit_signup_bonus: "Signup bonus",
  max_cashflow: "Cashflow",
  min_risk: "Low risk",
};

export const GOAL_ORDER: GoalKey[] = [
  "max_cashback",
  "max_travel",
  "credit_health",
  "hit_signup_bonus",
  "max_cashflow",
  "min_risk",
];

export interface EngineConstraints {
  max_utilization_bps: number | null;
  max_utilization_until: string | null;
  must_hit_bonus_card_ids: string[];
}

export interface EngineIntent {
  weights: Record<GoalKey, number>;
  constraints: EngineConstraints;
}

export interface EnginePurchase {
  id: string;
  amount_cents: number;
  category: string;
  date: string;
  is_recurring: boolean;
  locked_card_id: string | null;
}

// Engine cards carry more fields than we render; pass them through opaquely.
export interface EngineCard {
  id: string;
  name: string;
  credit_limit_cents: number;
  current_balance_cents: number;
  [key: string]: unknown;
}

export interface EngineScenario {
  id: string;
  name: string;
  reference_date: string;
  cards: EngineCard[];
  purchases: EnginePurchase[];
  intent: EngineIntent;
}

export interface ExplanationLine {
  kind: string;
  tone: "positive" | "neutral" | "caution";
  label: string;
  text: string;
  raw_value: number | boolean | null;
  unit: string | null;
  source_path: string;
  goal: GoalKey | null;
}

export interface AlternativeExplanation {
  card_id: string;
  card_name: string;
  feasible: boolean;
  summary: string;
  utility_delta_points: number | null;
  lines: ExplanationLine[];
}

export interface DecisionCard {
  card_id: string;
  card_name: string;
  purchase_id: string;
  purchase_label: string;
  headline: string;
  status: string;
  solver_method: string;
  factor_lines: ExplanationLine[];
  constraint_lines: ExplanationLine[];
  alternative: AlternativeExplanation | null;
  warning_lines: ExplanationLine[];
}

export interface CardSummary {
  card_id: string;
  assigned_purchase_ids: string[];
  assigned_spend_cents: number;
  ending_balance_cents: number;
  ending_utilization_bps: number;
  bonus_progress_cents: number;
  bonus_remaining_cents: number;
  bonus_hit: boolean;
}

export interface AllocationMetrics {
  projected_reward_value_cents: number;
  cashback_cents: number;
  travel_value_cents: number;
  max_card_utilization_bps: number;
  cashflow_value_cents: number;
  signup_bonus_hit_count: number;
}

export interface OptimizationIssue {
  code: string;
  message: string;
  suggestion: string | null;
}

export interface AllocationResult {
  status: string;
  solver_method: string;
  assignments: { purchase_id: string; card_id: string }[];
  card_summaries: CardSummary[];
  metrics: AllocationMetrics | null;
  issues: OptimizationIssue[];
  warnings: string[];
}

export interface AllocationExplanation {
  status: string;
  solver_method: string;
  headline: string;
  summary_lines: ExplanationLine[];
  decision_cards: DecisionCard[];
  highlighted_purchase_ids: string[];
  warning_lines: ExplanationLine[];
  failure: { headline: string; lines: ExplanationLine[]; suggestions: string[] } | null;
}

export interface FrontierPoint {
  label: string;
  weights_ppm: Record<GoalKey, number>;
  frontier_metrics: Partial<Record<GoalKey, number>>;
  allocation: AllocationResult;
}

export interface FrontierResult {
  solver_method: string;
  active_goal_ids: GoalKey[];
  swept_goal_ids: GoalKey[];
  grid_size: number;
  attempted_solves: number;
  successful_solves: number;
  complete_frontier: boolean;
  points: FrontierPoint[];
  warnings: string[];
}

export interface FrontierPointExplanation {
  label: string;
  summary: string;
  status: string;
  solver_method: string;
  metric_lines: ExplanationLine[];
}

export interface FrontierExplanation {
  headline: string;
  points: FrontierPointExplanation[];
  disclosure_lines: ExplanationLine[];
  warning_lines: ExplanationLine[];
}

export interface WhatIfResult {
  purchase_id: string;
  override_card_id: string;
  base_result: AllocationResult;
  override_result: AllocationResult;
  deltas: Record<string, number> | null;
  changed_assignments: { purchase_id: string; base_card_id: string; override_card_id: string }[];
}

export interface WhatIfExplanation {
  headline: string;
  base_status: string;
  override_status: string;
  delta_lines: ExplanationLine[];
  changed_assignment_lines: ExplanationLine[];
  warning_lines: ExplanationLine[];
  failure: { headline: string; lines: ExplanationLine[]; suggestions: string[] } | null;
}

export interface ParseIntentResult {
  intent: EngineIntent | null;
  source: string;
  provider_name: string;
  model_id: string;
  used_fallback: boolean;
  valid_model_output: boolean;
  warnings: { code: string; message: string }[];
}

interface Envelope<T> {
  data: T;
}

export const fetchDemoScenario = async (): Promise<EngineScenario> => {
  const res = await api<Envelope<{ scenario: EngineScenario }>>("/demo-scenario");
  return res.data.scenario;
};

export const parseIntent = async (
  text: string,
  cards: EngineCard[],
  reference_date?: string,
): Promise<ParseIntentResult> => {
  const res = await api<Envelope<{ result: ParseIntentResult }>>("/parse-intent", {
    method: "POST",
    body: JSON.stringify({ text, cards, reference_date: reference_date ?? null }),
  });
  return res.data.result;
};

export const allocateMonth = async (
  cards: EngineCard[],
  purchases: EnginePurchase[],
  intent: EngineIntent,
  solver_preference: "greedy" | "ilp" = "ilp",
): Promise<{ result: AllocationResult; explanation: AllocationExplanation }> => {
  const res = await api<Envelope<{ result: AllocationResult; explanation: AllocationExplanation }>>(
    "/allocate",
    {
      method: "POST",
      body: JSON.stringify({ cards, purchases, intent, solver_preference }),
    },
  );
  return res.data;
};

export const sampleFrontier = async (
  cards: EngineCard[],
  purchases: EnginePurchase[],
  intent: EngineIntent,
  max_points = 4,
): Promise<{ result: FrontierResult; explanation: FrontierExplanation }> => {
  const res = await api<Envelope<{ result: FrontierResult; explanation: FrontierExplanation }>>(
    "/frontier",
    {
      method: "POST",
      body: JSON.stringify({ cards, purchases, intent, solver_preference: "ilp", max_points }),
    },
  );
  return res.data;
};

export const runWhatIf = async (
  cards: EngineCard[],
  purchases: EnginePurchase[],
  intent: EngineIntent,
  purchase_id: string,
  override_card_id: string,
): Promise<{ result: WhatIfResult; explanation: WhatIfExplanation }> => {
  const res = await api<Envelope<{ result: WhatIfResult; explanation: WhatIfExplanation }>>(
    "/what-if",
    {
      method: "POST",
      body: JSON.stringify({
        cards,
        purchases,
        intent,
        purchase_id,
        override_card_id,
        solver_preference: "ilp",
      }),
    },
  );
  return res.data;
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
