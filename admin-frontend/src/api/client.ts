import { ADMIN_API_BASE, RAG_API_BASE } from '../config';
import { getCurrentLang } from '../i18n/current';
import type {
  AdminNotice,
  AdminDocument,
  AdminNoticeDetail,
  AdminProject,
  AdminProjectDetail,
  AdminRequirement,
  AdminUser,
  ComplianceStatus,
  IndexStatus,
  Overview,
  Paginated,
  Partition,
  QueuedTask,
  RequirementNotice,
  ResanitizeResult,
  SyncRun,
  SystemStatus,
} from './types';

/**
 * Client for the staff-only console API (`/api/admin/`).
 *
 * Auth is a Django session cookie, so every request is credentialed and every
 * unsafe request carries the CSRF token Django handed us. Nothing is stored in
 * localStorage — the session cookie is HttpOnly and the browser owns it.
 */

/**
 * A failed request, described by a stable `code` rather than a finished
 * sentence: the console must be able to re-render an error already on screen
 * after the operator switches language. `serverMessage` — localised by the
 * backend via `Accept-Language` — is the fallback for a code we do not map.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly serverMessage: string;
  /** Field-level validation errors, `{field: [{code, message}]}`. */
  readonly details: Record<string, { code?: string; message: string }[]>;

  constructor(
    code: string,
    status: number,
    serverMessage = '',
    details: Record<string, { code?: string; message: string }[]> = {},
  ) {
    super(serverMessage || code);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.serverMessage = serverMessage;
    this.details = details;
  }

  /** True when the session is missing or the account lacks staff rights. */
  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403;
  }
}

/** Codes derived from the status when the body carries none. */
const STATUS_CODES: Record<number, string> = {
  400: 'invalid',
  401: 'not_authenticated',
  403: 'permission_denied',
  404: 'not_found',
  405: 'method_not_allowed',
  429: 'throttled',
  500: 'server',
  502: 'service_unavailable',
  503: 'service_unavailable',
  504: 'service_unavailable',
};

/**
 * Normalise the envelope's `details` into `{field: [{code, message}]}`.
 *
 * The backend emits an object per field error since it began localising them;
 * plain strings are still accepted so a rolling deploy cannot break the UI.
 */
function readDetails(raw: unknown): Record<string, { code?: string; message: string }[]> {
  if (!raw || typeof raw !== 'object') return {};
  const result: Record<string, { code?: string; message: string }[]> = {};
  for (const [field, value] of Object.entries(raw as Record<string, unknown>)) {
    result[field] = [value].flat().map((entry) => {
      if (entry && typeof entry === 'object' && 'message' in entry) {
        const typed = entry as { code?: string; message: unknown };
        return { code: typed.code, message: String(typed.message) };
      }
      return { message: String(entry) };
    });
  }
  return result;
}

function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(^|;\\s*)${name}=([^;]*)`));
  return match ? decodeURIComponent(match[2]) : null;
}

async function ensureCsrfCookie(): Promise<void> {
  if (readCookie('csrftoken')) return;
  await fetch(`${ADMIN_API_BASE}/auth/csrf/`, { credentials: 'include' });
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  body?: unknown;
  params?: Record<string, string | number | undefined>;
  signal?: AbortSignal;
  /**
   * Prefix to call, defaulting to the console's own. The semantic index sits
   * under `/api/v1/` and is read by one screen here; giving it the parameter
   * rather than a second `request` implementation keeps the CSRF handling,
   * the error envelope and the `Accept-Language` header in one place.
   */
  base?: string;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, params, signal, base = ADMIN_API_BASE } = options;

  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && `${value}`.trim() !== '') {
      query.set(key, `${value}`);
    }
  }
  const suffix = query.toString();
  const url = `${base}${path}${suffix ? `?${suffix}` : ''}`;

  const headers: Record<string, string> = {
    Accept: 'application/json',
    // Lets the backend localise anything the console cannot map by code.
    'Accept-Language': getCurrentLang(),
  };
  if (method !== 'GET') {
    await ensureCsrfCookie();
    headers['X-CSRFToken'] = readCookie('csrftoken') ?? '';
    if (body !== undefined) headers['Content-Type'] = 'application/json';
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      credentials: 'include',
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('network', 0);
  }

  if (response.status === 204) return undefined as T;

  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const envelope = payload?.error;
    const code =
      (envelope?.code && String(envelope.code)) ||
      STATUS_CODES[response.status] ||
      'http';
    const serverMessage = envelope?.message
      ? String(envelope.message)
      : payload?.detail
        ? String(payload.detail)
        : '';

    throw new ApiError(code, response.status, serverMessage, readDetails(envelope?.details));
  }

  return payload as T;
}

// -- auth --------------------------------------------------------------------
export const login = (username: string, password: string) =>
  request<{ user: AdminUser }>('/auth/login/', {
    method: 'POST',
    body: { username, password },
  });

export const logout = () => request<void>('/auth/logout/', { method: 'POST' });

export const fetchMe = (signal?: AbortSignal) =>
  request<{ user: AdminUser }>('/auth/me/', { signal });

// -- dashboard ---------------------------------------------------------------
export const fetchOverview = (signal?: AbortSignal) =>
  request<Overview>('/overview/', { signal });

export const fetchSystemStatus = (signal?: AbortSignal) =>
  request<SystemStatus>('/system/', { signal });

// -- sync runs ---------------------------------------------------------------
export const fetchSyncRuns = (
  params: Record<string, string | number | undefined>,
  signal?: AbortSignal,
) => request<Paginated<SyncRun>>('/sync-runs/', { params, signal });

// -- partitions --------------------------------------------------------------
export const fetchPartitions = (
  params: Record<string, string | number | undefined>,
  signal?: AbortSignal,
) => request<Paginated<Partition>>('/partitions/', { params, signal });

export const rescanPartitions = () =>
  request<{ created: number; total: number }>('/partitions/rescan/', { method: 'POST' });

export const resetPartition = (id: number) =>
  request<Partition>(`/partitions/${id}/reset/`, { method: 'POST' });

export const runPartition = (id: number, pages?: number) =>
  request<QueuedTask>(`/partitions/${id}/run/`, {
    method: 'POST',
    body: pages ? { pages } : {},
  });

// -- notices -----------------------------------------------------------------
export const fetchNotices = (
  params: Record<string, string | number | undefined>,
  signal?: AbortSignal,
) => request<Paginated<AdminNotice>>('/notices/', { params, signal });

export const fetchNotice = (noticeId: string, signal?: AbortSignal) =>
  request<AdminNoticeDetail>(`/notices/${encodeURIComponent(noticeId)}/`, { signal });

export const resanitizeNotice = (noticeId: string) =>
  request<ResanitizeResult>(`/notices/${encodeURIComponent(noticeId)}/resanitize/`, {
    method: 'POST',
  });

// -- drill-down: projects and documents ---------------------------------------
export const fetchProjects = (
  params: Record<string, string | number | undefined>,
  signal?: AbortSignal,
) => request<AdminProject[]>('/projects/', { params, signal });

export const fetchProject = (projectId: string, signal?: AbortSignal) =>
  request<AdminProjectDetail>(`/projects/${encodeURIComponent(projectId)}/`, { signal });

export const fetchDocuments = (
  params: Record<string, string | number | undefined>,
  signal?: AbortSignal,
) => request<Paginated<AdminDocument>>('/documents/', { params, signal });

// -- requirements ------------------------------------------------------------
export const fetchRequirements = (
  params: Record<string, string | number | undefined>,
  signal?: AbortSignal,
) => request<Paginated<AdminRequirement>>('/requirements/', { params, signal });

export const fetchRequirementNotices = (signal?: AbortSignal) =>
  request<RequirementNotice[]>('/requirements/notices/', { signal });

// -- actions -----------------------------------------------------------------
export const triggerSync = (payload: {
  pages?: number;
  rows?: number;
  country?: string;
  method?: string;
}) => request<QueuedTask>('/actions/sync/', { method: 'POST', body: payload });

export const triggerBackfill = (payload: { pages?: number; partition_key?: string }) =>
  request<QueuedTask>('/actions/backfill/', { method: 'POST', body: payload });

/** Classify directions, mirror project documents/ESRS, parse awards, find sites. */
export const triggerEnrichment = (payload: {
  classify?: number;
  projects?: number;
  awards?: number;
  websites?: number;
}) => request<QueuedTask>('/actions/enrich/', { method: 'POST', body: payload });

// -- compliance --------------------------------------------------------------
/**
 * What the automatic extraction is doing right now.
 *
 * Safe to poll: the endpoint reads counts and never queues work, which is why
 * the compliance page refreshes it on a short timer while the sync pages use
 * the slower dashboard interval.
 */
export const fetchComplianceStatus = (signal?: AbortSignal) =>
  request<ComplianceStatus>('/compliance/', { signal });

export const triggerExtraction = (payload: { limit?: number; force?: boolean }) =>
  request<QueuedTask>('/actions/extract/', { method: 'POST', body: payload });

// -- semantic index ----------------------------------------------------------
/**
 * Qdrant's own numbers and the archive's coverage, read live.
 *
 * Under `/api/v1/` rather than `/api/admin/` — see `RAG_API_BASE`. The session
 * cookie is the same one, so nothing else about the call changes.
 */
export const fetchIndexStatus = (signal?: AbortSignal) =>
  request<IndexStatus>('/search/status/', { signal, base: RAG_API_BASE });
