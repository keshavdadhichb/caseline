/* Typed client for the Caseline backend. Every shape here mirrors what the
   API actually returns (see backend/app/main.py) — the UI renders live data
   only, never the design mock's placeholder figures. */

const BASE = "/api";

export interface PlanStep { tool: string; params: Record<string, unknown>; reason: string }
export interface PlanSkip { tool: string; reason: string }
export interface Plan {
  intent: string;
  filters: { window_days: number | null; date_from: string | null; date_to: string | null; min_amount: number | null; accounts: string[] | null };
  typologies: string[];
  steps: PlanStep[];
  skipped: PlanSkip[];
  clarification_needed: string | null;
  _served_from_cache?: boolean;
  _offline_fallback?: boolean;
}

/** One row of the thread's execution plan — carries the design's
 *  Chose / Because / Returned triple, all derived server-side. */
export interface NarratedStep {
  tool: string;
  name: string;
  state: "pending" | "running" | "done" | "error" | "skipped";
  skipped: boolean;
  skip_reason: string | null;
  output: string | null;
  elapsed_s: number | null;
  chose: string;
  because: string;
  returned: string;
}

export interface TypologyExplainer { name: string; what: string; rule: string; why: string }

export interface SubmitResponse {
  trace_id: string;
  plan: Plan;
  clarification_needed: string | null;
  prose?: string;
  steps?: NarratedStep[];
  /** True when the planner decided the question needs no data work at all. */
  conceptual?: boolean;
  typologies?: TypologyExplainer[] | null;
  /** Accounts the query named that do not exist in the dataset. */
  unknown_accounts?: string[];
  /** The plan is the generic offline fallback, not one built for this query. */
  degraded?: boolean;
  served_from_cache?: boolean;
}

export interface TraceEvent {
  step: string; state: string; summary: string | null; reason: string; elapsed_s?: number;
}
export interface TraceResponse { status: "running" | "done" | "error"; events: TraceEvent[] }

export interface RiskRecord {
  account_id: string;
  risk_level: "HIGH" | "MEDIUM" | "LOW";
  score: number;
  rules_fired: string[];
  graph_fired: string[];
  anomaly_component: number;
  anomaly_only: boolean;
  explanation: string;
}

export interface TimelineRow {
  ts: string; direction: "in" | "out"; counterparty: string;
  amount: number; channel: string; txn_id: string;
}
export interface RingEdge { from: string; to: string; amount: number }
export interface CaseFile {
  case_id: string;
  account_id: string;
  risk_level: "HIGH" | "MEDIUM" | "LOW";
  score: number;
  typologies: string[];
  evidence: Record<string, unknown>[];
  timeline: TimelineRow[];
  ring: { nodes: string[]; edges: RingEdge[] } | null;
  recommended_action: string;
  explanation: string;
  narrative: string | null;
}

export interface AggregationRow { account_id: string; count: number; total_amount: number }
export interface Aggregation {
  criteria: { min_count: number | null; max_amount: number | null; min_amount: number | null };
  matched: number;
  rows: AggregationRow[];
  truncated: boolean;
}
export interface Profile {
  n_txns: number; n_accounts: number; date_range: [string, string];
  total_volume: number; median_amount: number; channel_breakdown: Record<string, number>;
}

export interface ResultsResponse {
  results: RiskRecord[];
  cases: CaseFile[];
  prose?: string | null;
  steps?: NarratedStep[];
  conceptual?: boolean;
  /** Set when the question was answered by a count or a profile rather than
   *  by risk flags. */
  aggregation?: Aggregation | null;
  profile?: Profile | null;
}

export interface Stats {
  dataset: string;
  n_txns: number;
  n_accounts: number;
  date_range: [string, string];
  total_volume: number;
  median_amount: number;
  channels: Record<string, number>;
  typologies: string[];
  model: { name: string; seed: number; n_estimators: number; anomaly_top_percentile: number };
  graph: { fan_in_min_senders: number; fan_in_window_days: number };
  scoring_formula: string;
  determinism: string;
}

export interface Metrics { flags: number; tp: number; fp: number; fn: number; precision: number; recall: number; fpr: number }
export interface MethodResponse {
  split: string;
  ground_truth_accounts: number;
  universe_accounts: number;
  global: { baseline: Metrics; caseline: Metrics };
  tiers: Record<string, Metrics>;
  precision_at_n: Record<string, { n: number; hits: number; precision: number }>;
  patterns: { total_attempts_in_raw_file: number; applicable_to_test_split: number; detected: number; by_typology: Record<string, [number, number]> } | null;
  ring: { flagged: number; total: number; aggregator_caught: boolean };
  operational: Record<string, number>;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} — ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  submit: (query: string, clarification_answer?: string) =>
    json<SubmitResponse>("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, clarification_answer: clarification_answer ?? null }),
    }),
  trace: (id: string) => json<TraceResponse>(`/query/${id}/trace`),
  results: (id: string) => json<ResultsResponse>(`/query/${id}/results`),
  case: (id: string) => json<CaseFile>(`/case/${id}`),
  stats: () => json<Stats>("/stats"),
  method: () => json<MethodResponse>("/method"),
  exportUrl: (caseId: string) => `${BASE}/case/${caseId}/export`,
};

/* ---------- formatting helpers (currency in USD, per CLAUDE.md) ---------- */

export const usd = (n: number, decimals = 2) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: decimals, maximumFractionDigits: decimals });

export const compactUsd = (n: number) =>
  Math.abs(n) >= 1_000_000 ? `$${(n / 1_000_000).toFixed(1)}M` : usd(n, 0);

export const num = (n: number) => n.toLocaleString("en-US");
export const pct = (n: number, decimals = 1) => `${(n * 100).toFixed(decimals)}%`;

/** Human-facing typology label ("STRUCTURING_HIGH" -> "Structuring (strict)"). */
export const typologyLabel = (t: string) =>
  ({
    STRUCTURING_HIGH: "Structuring (confirmed)",
    STRUCTURING_MEDIUM: "Structuring (indicator)",
    RAPID_MOVEMENT: "Rapid movement",
    HIGH_RISK_AMOUNT: "High-risk amount",
    VELOCITY: "Velocity",
    FAN_IN_RING: "Fan-in ring",
    CYCLE: "Round-trip cycle",
  })[t] ?? t.replace(/_/g, " ").toLowerCase();

/** The design colours risk with two tints only — high (rose) and settled
 *  (green); medium/low reuse the neutral surface tint. */
export function riskTint(level: string) {
  if (level === "HIGH") return { bg: "var(--sev-high-bg)", fg: "var(--sev-high-fg)", dot: "var(--sev-high)" };
  if (level === "MEDIUM") return { bg: "var(--sev-med-bg)", fg: "var(--sev-med-fg)", dot: "var(--sev-med)" };
  return { bg: "var(--sev-ok-bg)", fg: "var(--sev-ok-fg)", dot: "var(--sev-ok)" };
}
