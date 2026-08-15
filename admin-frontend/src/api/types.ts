export interface AdminUser {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  is_staff: boolean;
  is_superuser: boolean;
  last_login: string | null;
}

export interface FacetCount {
  value: string;
  count: number;
}

export interface YearCount {
  year: number;
  count: number;
}

export interface SyncHealth {
  window_hours: number;
  runs_in_window: number;
  by_status: Record<string, number>;
  failures_in_window: number;
  last_run_at: string | null;
  last_run_status: string | null;
  last_success_at: string | null;
}

export interface ArchiveProgress {
  enabled: boolean;
  partitions_total: number;
  partitions_completed: number;
  partitions_pending: number;
  /** Share of partitions finished (row totals are unknown until first fetch). */
  percent: number;
  rows_walked: number;
  rows_reachable_known: number;
  notices_stored: number;
  upstream_total: number | null;
  complete: boolean;
}

export interface Overview {
  generated_at: string;
  notices: {
    total: number;
    open: number;
    closing_within_7_days: number;
    without_notice_date: number;
    earliest_notice_date: string | null;
    latest_notice_date: string | null;
    countries: number;
  };
  freshness: {
    last_synced_at: string | null;
    minutes_since_sync: number | null;
    stale: boolean;
  };
  notices_per_year: YearCount[];
  top_countries: FacetCount[];
  procurement_methods: FacetCount[];
  notice_types: FacetCount[];
  sync_health: SyncHealth;
  archive: ArchiveProgress;
}

export interface SystemStatus {
  database: { ok: boolean; vendor?: string; detail: string };
  cache: { ok: boolean; detail: string };
  celery: { ok: boolean; workers: string[]; detail: string };
  configuration: Record<string, string | number | boolean | null>;
}

export interface SyncRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  status: 'running' | 'success' | 'partial' | 'failed';
  trigger: string;
  pages_requested: number;
  pages_fetched: number;
  pages_failed: number;
  notices_seen: number;
  created_count: number;
  updated_count: number;
  unchanged_count: number;
  skipped_count: number;
  upstream_total: number | null;
  error_message: string;
}

export interface Partition {
  id: number;
  key: string;
  kind: 'recent' | 'country' | 'country_method';
  label: string;
  filters: Record<string, string>;
  status: 'pending' | 'running' | 'completed' | 'subdivided' | 'failed';
  next_offset: number;
  upstream_total: number | null;
  reachable_total: number | null;
  progress_percent: number;
  is_done: boolean;
  pages_done: number;
  pages_failed: number;
  created_count: number;
  updated_count: number;
  unchanged_count: number;
  last_error: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
}

export interface AdminNotice {
  id: string;
  notice_type: string;
  notice_status: string;
  country: string;
  project_id: string;
  bid_reference_no: string;
  bid_description: string;
  notice_date: string | null;
  deadline_date: string | null;
  procurement_method_code: string;
  sanitized_chars: number;
  raw_chars: number;
  last_synced_at: string;
  updated_at: string;
  source_url: string;
}

export interface AdminNoticeDetail extends AdminNotice {
  project_name: string;
  notice_language: string;
  submission_date: string | null;
  deadline_time: string;
  procurement_group: string;
  procurement_method_name: string;
  contact_name: string;
  contact_organization: string;
  contact_email: string;
  contact_phone_no: string;
  contact_address: string;
  contact_country: string;
  contact_web_url: string;
  content_hash: string;
  created_at: string;
  notice_text_sanitized: string;
  /** Untrusted upstream HTML — display as escaped text, never as markup. */
  notice_text_raw: string;
}

export interface Paginated<T> {
  count: number;
  total_pages: number;
  page: number;
  page_size: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface QueuedTask {
  queued: boolean;
  task_id: string;
  partition?: string;
}

export interface ResanitizeResult {
  notice_id: string;
  changed: boolean;
  chars_before: number;
  chars_after: number;
}

/**
 * One extraction run, as the console lists it.
 *
 * `requirements` and `status` answer different questions and are both needed:
 * a run that succeeded and found nothing is the normal case for three notices
 * in four, and it must not look like a run that failed.
 */
export interface ExtractionRunRow {
  id: number;
  notice_id: string;
  title: string;
  country: string;
  layers: string;
  status: 'ok' | 'failed' | string;
  model: string;
  requirements: number;
  /**
   * Expert positions the run read out of the same text (D20).
   *
   * Reported beside `requirements`, never added to it: a consulting REOI that
   * names its whole team and states no financial threshold is a successful run
   * with zero requirements, and one number would hide that it worked.
   */
  expert_positions: number;
  cost_usd: string;
  duration_ms: number;
  error: string;
  created_at: string;
}

/**
 * What the automatic extraction is doing.
 *
 * The counts are over *open* tenders only — around thirty at a time — so
 * `active_pending` is a queue that should drain within a cycle, not a
 * long-run coverage figure. `model_available` false is a working state, not a
 * fault: it means the free layer is running.
 *
 * `active_read` and `active_pending` are both measured against `layers` — the
 * depth this deployment currently runs — so configuring a key moves tenders
 * back into the queue rather than leaving them counted as done.
 * `active_stalled` is the remainder: attempted at this depth, never succeeded,
 * and now past the retry cap, so no press of the button will touch them.
 */
export interface ComplianceStatus {
  auto_extract: boolean;
  batch_size: number;
  layers: string;
  model_available: boolean;
  model: string;
  active_notices: number;
  active_read: number;
  active_pending: number;
  active_stalled: number;
  active_with_requirements: number;
  active_with_experts: number;
  runs_total: number;
  runs_failed: number;
  last_run_at: string | null;
  cost_usd: string;
  recent_runs: ExtractionRunRow[];
  checked_at: string;
}

/**
 * One extracted qualification requirement, with the tender it belongs to named
 * on the row.
 *
 * `summary` is the expression rendered as a sentence by the backend, not by the
 * console: the rendering has to match what the verdict is computed from, and a
 * second implementation in TypeScript would be a second thing to keep in step.
 * `expression` is the tree itself, kept for the operator auditing a verdict the
 * sentence makes look reasonable.
 */
export interface AdminRequirement {
  id: number;
  notice_id: string;
  notice_title: string;
  notice_country: string;
  notice_deadline: string | null;
  layer: string;
  key: string;
  label: string;
  summary: string;
  expression: unknown;
  applies_to: string;
  is_mandatory: boolean;
  grounding: string;
  evidence_quote: string;
  source: string;
  source_document_id: number | null;
  created_at: string;
}

/** A tender that has requirements, for the filter dropdown. */
export interface RequirementNotice {
  notice_id: string;
  title: string;
  requirements: number;
}

/** A project, aggregated from the notices that name it. */
export interface AdminProject {
  project_id: string;
  project_name: string;
  country: string;
  notices: number;
  open_notices: number;
  documents: number;
  requirements: number;
  latest_notice_date: string | null;
}

export interface AdminProjectNotice {
  notice_id: string;
  bid_description: string;
  notice_type: string;
  notice_status: string;
  notice_date: string | null;
  deadline_date: string | null;
  is_open: boolean | null;
  requirements: number;
  documents: number;
}

export interface AdminProjectDetail {
  project_id: string;
  project_name: string;
  country: string;
  notices: AdminProjectNotice[];
}

/**
 * A mirrored document. `notice_ids` is a list because identity is the URL —
 * one TOR is routinely linked by several notices of the same project.
 */
export interface AdminDocument {
  id: string;
  url: string;
  kind: string;
  status: string;
  origin: string;
  link_context: string;
  content_type: string;
  byte_size: number | null;
  text_chars: number;
  page_count: number | null;
  has_text_layer: boolean | null;
  parser: string;
  parse_error: string;
  http_status: number | null;
  last_error: string;
  fetched_at: string | null;
  created_at: string;
  notice_ids: string[];
  project_ids: string[];
  requirements: number;
}

/**
 * The semantic index, as `/api/v1/search/status/` reports it.
 *
 * Two objects rather than one flattened set of numbers, and the split is the
 * information: `collection` is what Qdrant says about itself, `archive` is what
 * Postgres says about how much of the mirror has been handed to it. They
 * disagree while an import is in flight, and they disagree permanently if the
 * collection was dropped without clearing the bookkeeping table — which is the
 * state the screen exists to make visible rather than average away.
 */
export interface IndexStatus {
  /** Whether embeddings can run at all here (switch on, key present). */
  enabled: boolean;
  collection_name: string;
  embed_model: string;
  pipeline_version: number;
  collection: {
    connected: boolean;
    exists: boolean;
    points: number;
    /** Lags `points` during an import — Qdrant indexes in the background. */
    indexed_vectors: number;
    segments: number;
    status: string;
    vector_size: number;
    distance: string;
    error: string;
  };
  /** Empty when the count could not be taken; the screen renders zeroes. */
  archive: Partial<{
    notices_total: number;
    documents_total: number;
    sources_total: number;
    notices_indexed: number;
    documents_indexed: number;
    failed: number;
  }>;
  last_indexed_at: string | null;
  /** Chunks this deployment believes it wrote. Compare against `points`. */
  chunks_recorded: number;
}
