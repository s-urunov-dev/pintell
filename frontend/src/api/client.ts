import { getCurrentLang } from '../i18n/current';
import type {
  AwardQuery,
  AwardRow,
  CompanyDetail,
  CompanyQuery,
  CompanyRow,
  ComplianceAssessment,
  ComplianceScore,
  DocumentKind,
  DocumentSubmission,
  Expert,
  ExpertFamily,
  ExpertQuery,
  Facets,
  NoticeExperts,
  NoticeRequirements,
  NoticeSource,
  Paginated,
  PendingProjectDocuments,
  ProjectDocuments,
  SimilarAwards,
  Stats,
  TeamLeadDetail,
  TenderDetail,
  TenderListItem,
  TenderQuery,
  VendorProfile,
  VendorProfileInput,
  ChatAnswer,
  ChatDraft,
  ChatStage,
  Conversation,
  ConversationMessage,
  StreamedClaim,
  SearchResponse,
  SearchResult,
  SourceText,
  VendorSession,
} from './types';

/**
 * Same-origin by default: the Vite dev server (dev) and nginx (docker) both
 * proxy `/api` to Django, so no cross-origin request is made from the browser.
 */
const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '') || '/api';

/**
 * A failed request, described by a stable `code` rather than a sentence.
 *
 * The UI must be able to re-render the same failure in another language after
 * the user switches, so nothing here is a finished message: `code` selects a
 * translated string, and `serverMessage` is only the last-resort fallback for
 * a code the frontend does not know.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly serverMessage: string;

  constructor(code: string, status: number, serverMessage = '') {
    super(serverMessage || code);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.serverMessage = serverMessage;
  }
}

function buildUrl(path: string, params?: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value !== undefined && value !== null && `${value}`.trim() !== '') {
      query.set(key, `${value}`);
    }
  }
  const suffix = query.toString();
  return `${API_BASE}${path}${suffix ? `?${suffix}` : ''}`;
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

/** A request body, when there is one. `undefined` means a plain GET. */
interface Payload {
  method: 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  /** JSON unless it is `FormData`, which goes up as multipart untouched.
   *  Absent for a DELETE, which carries nothing but its URL. */
  body?: unknown;
}

async function request<T>(url: string, signal?: AbortSignal, payload?: Payload): Promise<T> {
  // A file upload is the one body that must not be serialised or labelled: the
  // browser has to set `Content-Type` itself, because only it knows the
  // multipart boundary it generated. Setting the header by hand produces a
  // request the server cannot split into parts.
  const isMultipart = payload?.body instanceof FormData;

  let response: Response;
  try {
    response = await fetch(url, {
      signal,
      method: payload?.method,
      // The session cookie rides along on every call. Same-origin only: the
      // API is proxied under /api by nginx and by the dev server alike.
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        // Lets the backend localise anything the frontend cannot map by code.
        'Accept-Language': getCurrentLang(),
        ...(payload && payload.body !== undefined && !isMultipart
          ? { 'Content-Type': 'application/json' }
          : {}),
        ...(payload ? { 'X-CSRFToken': readCookie('csrftoken') } : {}),
      },
      body:
        payload && payload.body !== undefined
          ? isMultipart
            ? (payload.body as FormData)
            : JSON.stringify(payload.body)
          : undefined,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('network', 0);
  }

  if (!response.ok) {
    let code = STATUS_CODES[response.status] ?? 'http';
    let serverMessage = '';
    try {
      const body = await response.json();
      if (body?.error?.code) code = String(body.error.code);
      if (body?.error?.message) serverMessage = String(body.error.message);
      else if (body?.detail) serverMessage = String(body.detail);
    } catch {
      /* response had no JSON body — the status-derived code stands */
    }
    throw new ApiError(code, response.status, serverMessage);
  }

  // 204 is a real answer with no body — a successful delete says nothing.
  // Parsing it as JSON would turn a success into a thrown SyntaxError.
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function fetchTenders(
  query: TenderQuery,
  signal?: AbortSignal,
): Promise<Paginated<TenderListItem>> {
  return request(buildUrl('/tenders/', query as Record<string, string | number | undefined>), signal);
}

export function fetchTender(noticeId: string, signal?: AbortSignal): Promise<TenderDetail> {
  return request(buildUrl(`/tenders/${encodeURIComponent(noticeId)}/`), signal);
}

export function fetchFacets(signal?: AbortSignal): Promise<Facets> {
  return request(buildUrl('/tenders/facets/'), signal);
}

/**
 * Every document of the notice's parent project (title + PDF), plus the ESRS
 * summary. Returns a `pending` marker when the project has not been mirrored
 * yet, so the UI can say "being fetched" rather than "none".
 */
export function fetchNoticeDocuments(
  noticeId: string,
  signal?: AbortSignal,
): Promise<ProjectDocuments | PendingProjectDocuments> {
  return request(buildUrl(`/tenders/${encodeURIComponent(noticeId)}/documents/`), signal);
}

export function fetchStats(signal?: AbortSignal): Promise<Stats> {
  return request(buildUrl('/tenders/stats/'), signal);
}

export function fetchAwards(
  query: AwardQuery,
  signal?: AbortSignal,
): Promise<Paginated<AwardRow>> {
  return request(
    buildUrl('/awards/', query as Record<string, string | number | undefined>),
    signal,
  );
}

/** Finished contracts most like one open tender. Scored server-side; an empty
 *  list is a normal answer and the caller renders nothing for it. */
export function fetchSimilarAwards(
  noticeId: string,
  signal?: AbortSignal,
): Promise<SimilarAwards> {
  return request(
    buildUrl(`/tenders/${encodeURIComponent(noticeId)}/similar-awards/`),
    signal,
  );
}

export function fetchCompanies(
  query: CompanyQuery,
  signal?: AbortSignal,
): Promise<Paginated<CompanyRow>> {
  return request(
    buildUrl('/companies/', query as Record<string, string | number | undefined>),
    signal,
  );
}

export function fetchCompany(name: string, signal?: AbortSignal): Promise<CompanyDetail> {
  return request(buildUrl(`/companies/${encodeURIComponent(name)}/`), signal);
}

export function fetchTeamLead(id: string, signal?: AbortSignal): Promise<TeamLeadDetail> {
  return request(buildUrl(`/team-leads/${encodeURIComponent(id)}/`), signal);
}

/* -------------------------------------------------------------------------
   Compliance
   ------------------------------------------------------------------------- */

/**
 * The compliance API sits under its own prefix rather than beside the tender
 * routes: `/api/` mirrors published data, while this one accepts what a vendor
 * says about itself, and the two carry different privacy questions.
 */
const COMPLIANCE_BASE = `${API_BASE}/compliance`;

function complianceUrl(path: string): string {
  return `${COMPLIANCE_BASE}${path}`;
}

/* -------------------------------------------------------------------------
   Vendor accounts
   -------------------------------------------------------------------------
   Session cookies, not tokens. The site and the API are same-origin (nginx
   proxies /api), so the browser holds the session the way it holds any other
   login, and no credential is ever stored where a script can read it.

   The cost of sessions is CSRF, which Django enforces on every unsafe method.
   `csrfHeader` reads the cookie Django set and echoes it back — the standard
   double-submit — and `ensureCsrfCookie` asks for one when the visitor has
   never had a session at all.
   ------------------------------------------------------------------------- */

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : '';
}

async function ensureCsrfCookie(): Promise<void> {
  if (readCookie('csrftoken')) return;
  await fetch(complianceUrl('/auth/csrf/'), { credentials: 'same-origin' });
}

/** The signed-in vendor, or `null`. Called once on boot. */
export function fetchVendorSession(signal?: AbortSignal): Promise<VendorSession> {
  return request(complianceUrl('/auth/me/'), signal);
}

export async function registerVendor(
  payload: { email: string; password: string; name: string; country?: string },
): Promise<VendorSession> {
  await ensureCsrfCookie();
  return request(complianceUrl('/auth/register/'), undefined, {
    method: 'POST',
    body: payload,
  });
}

export async function loginVendor(
  payload: { email: string; password: string },
): Promise<VendorSession> {
  await ensureCsrfCookie();
  return request(complianceUrl('/auth/login/'), undefined, {
    method: 'POST',
    body: payload,
  });
}

export async function logoutVendor(): Promise<void> {
  await ensureCsrfCookie();
  return request(complianceUrl('/auth/logout/'), undefined, { method: 'POST', body: {} });
}

/** The signed-in vendor's own profile. There is no id: see the API docstring. */
export function fetchMyProfile(signal?: AbortSignal): Promise<VendorProfile> {
  return request(complianceUrl('/profile/'), signal);
}

/** PATCH rather than PUT: a profile is filled in over time, a field at a time. */
export async function updateMyProfile(
  profile: Partial<VendorProfileInput>,
): Promise<VendorProfile> {
  await ensureCsrfCookie();
  return request(complianceUrl('/profile/'), undefined, {
    method: 'PATCH',
    body: profile,
  });
}

/** What the tender asks for, with no vendor involved. */
export function fetchNoticeRequirements(
  noticeId: string,
  signal?: AbortSignal,
): Promise<NoticeRequirements> {
  return request(
    complianceUrl(`/notices/${encodeURIComponent(noticeId)}/requirements/`),
    signal,
  );
}

/**
 * Assess the signed-in vendor against one notice.
 *
 * No body: the tender is in the URL and the vendor is in the session. A POST
 * rather than a GET because the *response* carries their declared finances
 * beside a verdict, and GETs are what caches and shared histories keep.
 */
export async function assessNotice(
  noticeId: string,
  signal?: AbortSignal,
): Promise<ComplianceAssessment> {
  await ensureCsrfCookie();
  return request(
    complianceUrl(`/notices/${encodeURIComponent(noticeId)}/assessment/`),
    signal,
    { method: 'POST', body: {} },
  );
}

/**
 * Save what the vendor says they have.
 *
 * A list rather than one request per switch: a vendor works down a page of
 * criteria, and per-switch requests would make the saved state depend on the
 * order the network happened to deliver them in.
 *
 * `satisfied: null` withdraws an answer rather than setting it to "no" — a box
 * ticked by mistake must be correctable to *unanswered*, which is not a claim.
 */
/**
 * The text the criteria were read out of, with each quote's position in it.
 *
 * Public, like the requirements it accompanies: these are the borrower's own
 * published words, and a vendor is entitled to read them before signing up.
 */
export function fetchNoticeSource(
  noticeId: string,
  signal?: AbortSignal,
): Promise<NoticeSource> {
  return request(
    complianceUrl(`/notices/${encodeURIComponent(noticeId)}/document/`),
    signal,
  );
}

/**
 * Where the mirrored file itself is served from.
 *
 * A URL rather than a fetch, because PDF.js does its own ranged requests
 * against it. Served by us rather than by the borrower for the reason the
 * mirror exists at all: the original link is usually dead by the time anyone
 * reads the tender.
 */
export function documentFileUrl(documentId: string): string {
  return complianceUrl(`/documents/${encodeURIComponent(documentId)}/file/`);
}

/**
 * Save a vendor's answers, and get the recomputed readiness back with them.
 *
 * The score travels in the *write* response on purpose. The indicator is
 * answering the switch, and the figure behind it is weighted by an importance
 * the client never sees and derived from verdicts the engine reaches over the
 * whole set — so the alternative to this field is either a bar that lags the
 * control by a second request, or a second implementation of the arithmetic in
 * TypeScript that would drift from the one that counts.
 */
export async function declareRequirements(
  noticeId: string,
  entries: { requirement_id: number; satisfied: boolean | null }[],
  signal?: AbortSignal,
): Promise<{ declarations: Record<string, boolean>; score: ComplianceScore }> {
  await ensureCsrfCookie();
  return request(
    complianceUrl(`/notices/${encodeURIComponent(noticeId)}/declarations/`),
    signal,
    { method: 'POST', body: entries },
  );
}

/**
 * Hand over the tender document the notice never linked.
 *
 * Most notices state no criteria because the criteria are in a Terms of
 * Reference the notice only names; the notice publishes a contact so a bidder
 * can ask for it. This is the other end of that loop — the vendor passes on
 * what the contact sent them, as a file or as the link they were given.
 *
 * The request is deliberately unbounded in time: extraction runs inline, so a
 * long document means a long wait rather than a job the vendor cannot see.
 */
export async function submitNoticeDocument(
  noticeId: string,
  submission: ({ file: File } | { url: string }) & { kind?: DocumentKind },
  signal?: AbortSignal,
): Promise<DocumentSubmission> {
  await ensureCsrfCookie();
  const url = complianceUrl(`/notices/${encodeURIComponent(noticeId)}/documents/`);
  // The kind is what slot the vendor dropped the file into. It is a claim about
  // the document, not a fact we verified — the backend files it as told and the
  // extractor reads whatever is there regardless, so a mislabelled upload costs
  // an ordering preference in `pipeline._DOCUMENT_PRIORITY` and nothing else.
  const kind = submission.kind ?? 'tor';
  if ('file' in submission) {
    const form = new FormData();
    form.append('file', submission.file);
    form.append('kind', kind);
    return request(url, signal, { method: 'POST', body: form });
  }
  return request(url, signal, { method: 'POST', body: { url: submission.url, kind } });
}

/* -------------------------------------------------------------------------
   Expert directory
   -------------------------------------------------------------------------
   Under `/api/` beside the tenders rather than under `/api/compliance/`, and
   public like they are: everything served is what each person already
   publishes about their own professional life.
   ------------------------------------------------------------------------- */

/** The taxonomy, as five families each holding its roles. Never paginated. */
export function fetchExpertTypes(signal?: AbortSignal): Promise<ExpertFamily[]> {
  return request(buildUrl('/experts/types/'), signal);
}

/**
 * The directory, filtered and sorted by the client.
 *
 * `role` is repeatable and reads as a union — one seat, several acceptable
 * roles — so it is appended by hand rather than passed through `buildUrl`,
 * which holds one value per key.
 */
export function fetchExperts(
  query: ExpertQuery,
  signal?: AbortSignal,
): Promise<Paginated<Expert>> {
  const { role, ...rest } = query;
  const url = buildUrl('/experts/', rest as Record<string, string | number | undefined>);
  const roles = (role ?? []).filter(Boolean);
  if (roles.length === 0) return request(url, signal);
  const separator = url.includes('?') ? '&' : '?';
  const repeated = roles.map((slug) => `role=${encodeURIComponent(slug)}`).join('&');
  return request(`${url}${separator}${repeated}`, signal);
}

/**
 * The team one tender names, and who the directory holds for those roles.
 *
 * Public, like the requirements endpoint: this is what the borrower published,
 * and a vendor should be able to read it before deciding to sign up.
 */
export function fetchNoticeExperts(
  noticeId: string,
  signal?: AbortSignal,
): Promise<NoticeExperts> {
  return request(complianceUrl(`/notices/${encodeURIComponent(noticeId)}/experts/`), signal);
}

/* -------------------------------------------------------------------------- */
/* Semantic search                                                             */
/* -------------------------------------------------------------------------- */
/**
 * Its own prefix, and the prefix is the contract.
 *
 * Everything under `/api/` is a read of the mirror and answers the same way
 * whether or not anything has been embedded. This one answers out of an index
 * that may be empty, stale or rebuilt overnight — and its payloads carry the
 * page coordinates this app draws highlights from, so a breaking change there
 * ships as `/api/v2/` rather than as a surprise.
 */
const SEARCH_BASE = '/v1/search';

/**
 * Ask the archive a question.
 *
 * POST although it reads nothing: the query is free text, and a body keeps it
 * out of the URL, the access log and the browser history.
 *
 * The response says which retrieval path answered. A caller that ignores
 * `retrieval` is not wrong, but a caller that *compares* scores across two
 * responses is — see `SearchResult.score`.
 */
export function searchArchive(
  query: string,
  options: {
    noticeId?: string;
    category?: string;
    subcategory?: string;
    sourceType?: 'pdf' | 'text';
    limit?: number;
  } = {},
  signal?: AbortSignal,
): Promise<SearchResponse> {
  return request(
    buildUrl(`${SEARCH_BASE}/vector/`),
    signal,
    {
      method: 'POST',
      body: {
        query,
        notice_id: options.noticeId,
        category: options.category,
        subcategory: options.subcategory,
        source_type: options.sourceType,
        limit: options.limit,
      },
    },
  );
}

/**
 * The exact string a text citation's `char_start`/`char_end` index.
 *
 * Fetched rather than derived from the notice body this app already has,
 * because those offsets are into the *canonical* form — entities unescaped,
 * lookalike punctuation folded, whitespace collapsed, block tags turned into
 * full stops. Re-deriving that here would be a second implementation of a
 * Python function, and it would fail silently: the highlight would be a few
 * characters out at the first `&nbsp;` and further out with every one after.
 *
 * PDF citations never call this — they are drawn on the file itself.
 */
export function fetchSourceText(
  sourceKey: string,
  signal?: AbortSignal,
): Promise<SourceText> {
  return request(buildUrl(`${SEARCH_BASE}/source/`, { source_key: sourceKey }), signal);
}

/**
 * Ask the archive a question and get sentences back, each with its passages.
 *
 * The answer is claims and sources rather than a paragraph, so the UI never
 * parses citations out of prose: each claim already names the passages that
 * support it, by index into `sources`.
 */
export function askArchive(
  question: string,
  options: { noticeId?: string; category?: string; conversationId?: string } = {},
  signal?: AbortSignal,
): Promise<ChatAnswer> {
  return request(buildUrl('/v1/chat/'), signal, {
    method: 'POST',
    body: {
      question,
      notice_id: options.noticeId,
      category: options.category,
      conversation_id: options.conversationId,
    },
  });
}

/**
 * The same question, with the pipeline reporting itself as it runs.
 *
 * `onStage` receives the server's own stages — the embedding call starting,
 * the number of passages actually selected, the model call beginning — so the
 * waiting state a reader watches is a report rather than a guess. When the
 * stream cannot be opened at all the caller falls back to `askArchive`: the
 * answer matters more than the narration of it.
 *
 * Parsed by hand rather than with `EventSource`, because `EventSource` is
 * GET-only and this request carries a body (the question, the thread, the
 * scope) that has no business in a URL or an access log.
 */
export async function askArchiveStreaming(
  question: string,
  options: { noticeId?: string; category?: string; conversationId?: string } = {},
  onStage: (stage: ChatStage) => void,
  onClaim: (claim: StreamedClaim) => void,
  onDraft: (draft: ChatDraft) => void,
  onSources: (sources: SearchResult[]) => void,
  signal?: AbortSignal,
): Promise<ChatAnswer> {
  let response: Response;
  try {
    response = await fetch(buildUrl('/v1/chat/stream/'), {
      signal,
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        'Accept-Language': getCurrentLang(),
        'X-CSRFToken': readCookie('csrftoken'),
      },
      body: JSON.stringify({
        question,
        notice_id: options.noticeId,
        category: options.category,
        conversation_id: options.conversationId,
      }),
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError('network', 0);
  }

  if (!response.ok || !response.body) {
    throw new ApiError(STATUS_CODES[response.status] ?? 'http', response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let answer: ChatAnswer | null = null;
  let failure = '';

  // SSE frames are separated by a blank line, and a frame can be split across
  // any number of network chunks — so the buffer is drained frame by frame
  // rather than read line by line as the chunks arrive.
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf('\n\n');
    while (boundary !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf('\n\n');

      let event = 'message';
      const data: string[] = [];
      for (const line of frame.split('\n')) {
        // A comment line is the server's keep-alive. Ignored, as the spec says.
        if (line.startsWith(':')) continue;
        if (line.startsWith('event: ')) event = line.slice(7).trim();
        else if (line.startsWith('data: ')) data.push(line.slice(6));
      }
      if (data.length === 0) continue;

      let payload: unknown;
      try {
        payload = JSON.parse(data.join('\n'));
      } catch {
        continue;
      }

      if (event === 'stage') onStage(payload as ChatStage);
      // A claim the model has finished writing, already checked against the
      // sources it may cite. Rendered as it arrives; the `answer` event that
      // follows is still the authoritative, whole result.
      else if (event === 'claim') onClaim(payload as StreamedClaim);
      // The sentence in progress, growing token by token. Rendered as
      // unfinished — see `ChatDraft`.
      else if (event === 'draft') onDraft(payload as ChatDraft);
      // The passages, before a word is written. They arrive early so a
      // streamed sentence can be drawn *with* its citation badges rather than
      // gaining them afterwards, which reads as the answer re-rendering.
      else if (event === 'sources') {
        onSources((payload as { sources: SearchResult[] }).sources);
      }
      else if (event === 'answer') answer = payload as ChatAnswer;
      else if (event === 'error') failure = String((payload as { reason?: string }).reason ?? '');
    }
  }

  if (!answer) throw new ApiError(failure ? 'server' : 'network', 0);
  return answer;
}

/** The reader's saved threads, newest first. */
export function listConversations(signal?: AbortSignal): Promise<{ results: Conversation[] }> {
  return request(buildUrl('/v1/chat/conversations/'), signal);
}

/** One thread with every turn it holds, in the order they were spoken. */
/**
 * One thread, newest turns first page.
 *
 * `before` walks backwards by message id rather than by offset: an offset
 * shifts when a turn is added while the reader is scrolling back, and the page
 * that shifts is the one they are reading.
 */
export function getConversation(
  id: string,
  options: { before?: number } = {},
  signal?: AbortSignal,
): Promise<{
  conversation: Conversation;
  messages: ConversationMessage[];
  has_more: boolean;
  oldest_id: number | null;
}> {
  return request(
    buildUrl(`/v1/chat/conversations/${encodeURIComponent(id)}/`, {
      before: options.before,
    }),
    signal,
  );
}

export function renameConversation(
  id: string,
  title: string,
  signal?: AbortSignal,
): Promise<Conversation> {
  return request(buildUrl(`/v1/chat/conversations/${encodeURIComponent(id)}/`), signal, {
    method: 'PATCH',
    body: { title },
  });
}

export function deleteConversation(id: string, signal?: AbortSignal): Promise<void> {
  return request(buildUrl(`/v1/chat/conversations/${encodeURIComponent(id)}/`), signal, {
    method: 'DELETE',
  });
}

