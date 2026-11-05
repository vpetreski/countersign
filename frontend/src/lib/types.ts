// Response shapes of the Countersign API. The backend views (countersign/api/views.py) are the source of truth.

export type Role = "viewer" | "operator" | "approver";
export type Provider = "aws" | "azure" | (string & {});
export type Risk = "low" | "medium" | "high";

export interface Persona {
  key: string;
  name: string;
  role: Role;
  description: string;
}

export interface Workspace {
  workspace_id: string;
  expires_at: string;
  tokens: Record<string, string>;
  personas: Persona[];
}

export interface FaultInfo {
  name: string;
  title: string;
  effect: string;
  expected: string;
}

export interface PolicyRule {
  id: string;
  name: string;
  description: string;
}

export interface AppConfig {
  name: string;
  version: string;
  model: { provider: string; model: string; keyless: boolean; reason: string | null };
  build: { sha: string; environment: string };
  faults: FaultInfo[];
  personas: Persona[];
  limits: {
    max_model_turns: number;
    max_tool_calls: number;
    runs_per_hour: number;
    workspace_ttl_hours: number;
  };
  policy: { version: number; rules: PolicyRule[] };
}

export type RunStatus =
  | "created"
  | "planning"
  | "awaiting_input"
  | "awaiting_approval"
  | "executing"
  | "verifying"
  | "completed"
  | "completed_with_failures"
  | "failed"
  | "rejected"
  | "cancelled"
  | "budget_exhausted"
  | "expired";

export interface Budgets {
  model_turns: number;
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  planning_ms: number;
  plan_revisions: number;
  model_retries: number;
  max_model_turns: number;
  max_tool_calls: number;
  max_input_tokens: number;
  max_wall_ms: number;
  max_plan_revisions: number;
  simulated_tokens: boolean;
}

export interface Evidence {
  id: string;
  tool: string;
  ref: string;
  kind: string;
  summary: string;
  turn?: number;
}

export interface PolicyCheck {
  rule: string;
  name: string;
  passed: boolean;
  detail: string;
}

export type StepStatus = "done" | "replayed" | "failed" | "compensated";

export interface StepResult {
  step: string;
  status: StepStatus;
  idempotency_key: string;
  attempts: number;
  detail: string;
  at: string;
}

export type OutcomeStatus = "pending" | "succeeded" | "failed" | "compensated" | "rejected" | "skipped";

export interface ActionOutcome {
  action_id: string;
  status: OutcomeStatus;
  steps: StepResult[];
  verified: boolean | null;
  realized_monthly_savings: number;
  detail: string;
}

export interface PlanAction {
  id: string;
  ref: string;
  resource_id: string;
  name: string;
  provider: Provider;
  kind: string;
  environment: string;
  team: string | null;
  owner: string | null;
  operation: string;
  current_sku: string;
  target_sku: string | null;
  monthly_cost: number | null;
  est_monthly_savings: number;
  price_source: string | null;
  risk: Risk;
  reversible: boolean;
  steps: string[];
  evidence_ids: string[];
  rationale: string;
  checks: PolicyCheck[];
  warnings: string[];
  approvals: number;
  rejections: number;
  outcome: ActionOutcome | null;
}

/** A finding the agent reviewed and left out of the change request, with its reason. */
export interface NotProposed {
  ref: string;
  reason: string;
  evidence_ids: string[];
}

export interface Plan {
  hash: string;
  summary: string;
  caveats: string[];
  not_proposed?: NotProposed[];
  total_monthly_savings: number;
  risk: Risk;
  approvals_required: number;
  approvals_received: number;
  revision: number;
  policy_version: number;
  actions: PlanAction[];
}

export type Verdict = "approve" | "reject";

export interface Decision {
  approver: string;
  persona: string;
  decided_at: string;
  plan_hash: string;
  decisions: Record<string, Verdict>;
  note: string | null;
}

export interface Question {
  tool_use_id: string;
  text: string;
  options: string[];
}

export interface RunCan {
  advance: boolean;
  answer: boolean;
  decide: boolean;
  cancel: boolean;
}

export interface RunView {
  id: string;
  status: RunStatus;
  terminal: boolean;
  version: number;
  request: string;
  created_by: string;
  model: string;
  created_at: string;
  updated_at: string;
  faults: string[];
  degraded: string[];
  error: string | null;
  report: string | null;
  question: Question | null;
  budgets: Budgets;
  not_proposed?: NotProposed[];
  waiting_for: "operator" | "approver" | null;
  approvals: Decision[];
  can: RunCan;
  plan: Plan | null;
  evidence?: Record<string, Evidence>;
  realized_monthly_savings?: number;
}

export interface RunEvent {
  seq: number;
  at: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface Span {
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  name: string;
  kind: string;
  start_ns: number;
  end_ns: number;
  status: string;
  attributes: Record<string, unknown>;
}

export interface TraceResponse {
  trace_id: string | null;
  spans: Span[];
}

export interface CategoryWaste {
  count: number;
  monthly_usd: number;
  eligible_count: number;
  eligible_monthly_usd: number;
}

export interface Dashboard {
  data_as_of: string;
  resources: {
    total: number;
    by_provider: Record<string, Record<string, number>>;
    by_environment: Record<string, number>;
    accounts: number;
  };
  spend: {
    period: string;
    by_provider: Record<string, number>;
    total: number;
    daily: Array<{ day: string; [provider: string]: number | string }>;
  };
  waste: {
    monthly_usd: number;
    eligible_monthly_usd: number;
    findings: number;
    eligible: number;
    by_category: Record<string, CategoryWaste>;
    blocked_by: Record<string, number>;
  };
  data_health: { checks: Record<string, number>; quarantined: number };
  changed_in_workspace: string[];
}

export interface Utilization {
  window_days: number;
  expected_days: number;
  datapoints: number;
  coverage: number;
  avg: number;
  p95_daily_avg: number;
  max: number;
  last_datapoint: string | null;
}

export interface Finding {
  ref: string;
  resource_id: string;
  name: string;
  provider: Provider;
  kind: string;
  account: string;
  region: string;
  environment: string;
  team: string | null;
  owner: string | null;
  category: string;
  operation: string | null;
  eligible: boolean;
  blockers: string[];
  warnings: string[];
  current_sku: string | null;
  target_sku: string | null;
  monthly_cost_usd: number | null;
  est_monthly_savings_usd: number | null;
  price_source: string | null;
  price_reference: string | null;
  utilization: Utilization | null;
  unattached_days: number | null;
  flagged_untrusted_text: boolean;
  flags: string[];
}

export interface Resource {
  id: string;
  ref: string;
  provider: Provider;
  kind: string;
  name: string;
  account: string;
  region: string;
  environment: string;
  team: string | null;
  owner: string | null;
  tags: Record<string, string>;
  state: string;
  native_state: string;
  sku: string | null;
  size_gb: number | null;
  created_at: string | null;
  detached_at: string | null;
  attached_to: string | null;
  flags: string[];
  source: string;
  source_ref: string;
}

export interface ResourceDetail {
  resource: Resource;
  finding: Finding | null;
  cpu: Array<{ day: string; avg: number | null; max: number | null }>;
  daily_cost: Array<{ day: string; billed_usd: number }>;
}

export interface SourceSnapshot {
  source: string;
  checksum: string;
  fetched_at: string;
  data_as_of: string;
  records_read: number;
  quarantined: number;
  warnings: number;
}

export interface QualityCheck {
  check_id: string;
  category: string;
  status: string;
  observed: string;
  expected: string;
  detail: string;
}

export interface QuarantineRow {
  source: string;
  source_ref: string;
  reason: string;
  raw: string;
}

export interface SourcesResponse {
  snapshots: SourceSnapshot[];
  checks: QualityCheck[];
  quarantine: QuarantineRow[];
}

export interface AuditEntry {
  seq: number;
  at: string;
  actor: string;
  action: string;
  subject: string;
  payload: unknown;
  prev_hash: string;
  hash: string;
}

export interface AuditVerify {
  valid: boolean;
  entries: number;
  head: string | null;
  broken_at: number | null;
  reason: string | null;
}

export interface EvalCriterion {
  mean?: number;
  ci_method?: string;
  n_cases?: number;
  cases_all_pass_rate?: number;
  ci95?: number[];
  cases_all_pass?: number;
  cases_all_pass_wilson?: number[];
}

export interface EvalMutant {
  description?: string;
  expected_grader?: string;
  caught_by?: string[];
  caught_by_expected?: boolean;
  scores?: Record<string, number>;
  discordant_cases?: Record<string, number>;
  p_value?: Record<string, number>;
  significant?: Record<string, boolean>;
  /** One reason string per criterion that caught the mutant. */
  example?: Record<string, string>;
}

export interface EvalCase {
  id: string;
  title?: string;
  tags?: string[];
  outcomes?: Record<string, number[]>;
  passed_all?: boolean;
  failures?: Record<string, unknown>;
  statuses?: string[];
}

export interface EvalCalibration {
  simulator?: string;
  noise?: number;
  trials?: number;
  cases?: number;
  truth?: { task_completion_mean?: number; pass_hat_k?: Record<string, number> };
  estimate?: { task_completion_mean?: number; ci95?: number[]; pass_hat_k?: Record<string, number> };
  covered?: boolean;
  realized?: { slips?: number; chances?: number; rate?: number; z?: number; note?: string };
  other_criteria_unaffected?: boolean;
  coverage?: {
    replicates?: number;
    nominal?: number;
    mean_interval_coverage?: number;
    all_pass_wilson_coverage?: number;
    monte_carlo_se?: number;
    mean_interval_width?: number;
  };
}

export interface EvalReport {
  honesty?: string;
  generated_at?: string;
  model?: string | { name?: string; provider?: string; simulated?: boolean };
  trials?: number;
  n_cases?: number;
  fingerprint?: { combined?: string; [key: string]: unknown };
  criteria?: Record<string, EvalCriterion>;
  pass_hat_k?: Record<string, number>;
  mde?: { discordant_flips?: number; alpha?: number; n_cases?: number; test?: string; note?: string };
  baseline?: { regressions?: unknown[]; improvements?: unknown[] };
  mutation?: {
    score?: number;
    killed?: number;
    total?: number;
    reference_false_positives?: number;
    graders?: string[];
    mutants?: Record<string, EvalMutant>;
  };
  cases?: EvalCase[];
  calibration?: EvalCalibration;
  overall?: { cases_all_pass?: number; cases_all_pass_wilson?: number[] };
  runs?: { total?: number; wall_seconds?: number; p95_trial_ms?: number };
}
