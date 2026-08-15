export type TenderCategory =
  | 'construction'
  | 'consulting'
  | 'supply'
  | 'services'
  | 'it'
  | 'other'
  | 'unknown';

/**
 * The submission deadline pinned to a real instant.
 *
 * Upstream publishes a date and a bare wall-clock string with no zone, so the
 * backend derives the zone from the notice's country. `null` when it could not
 * — the UI then shows whole days rather than a countdown it cannot justify.
 */
export interface ResolvedDeadline {
  /** ISO instant to count down to. */
  at: string;
  /** IANA zone the local time was read in, e.g. "Asia/Tashkent". */
  timezone: string;
  /** The published local time ("17:00"), or "" when none was given. */
  local_time: string;
  /** True when the country spans several zones and the capital's was used. */
  approximate: boolean;
}

/** Sub-directions inside Consulting. Consulting is broad enough that a firm
 *  cannot act on it alone; the other five directions are not split. */
export type ConsultingSubcategory =
  | 'engineering'
  | 'audit'
  | 'environment_social'
  | 'training'
  | 'research'
  | 'it_advisory'
  | 'legal_procurement'
  | 'management'
  | 'other';

/** Who a consulting notice is addressed to — the other axis of the same split.
 *  `''` is not "unclassified consulting": it is either a non-consulting notice
 *  or a selection method that serves both audiences, so it must never be
 *  offered as a filter value. */
export type ConsultingAudience = 'firm' | 'individual';

export interface TenderListItem {
  id: string;
  source: string;
  notice_type: string;
  notice_status: string;
  notice_date: string | null;
  deadline_date: string | null;
  deadline_time: string;
  deadline: ResolvedDeadline | null;
  country: string;
  project_id: string;
  project_name: string;
  bid_reference_no: string;
  bid_description: string;
  procurement_group: string;
  procurement_method_code: string;
  procurement_method_name: string;
  category: TenderCategory;
  category_source: string;
  category_confidence: number | null;
  /** Sub-direction — only set on Consulting notices; "" elsewhere. */
  subcategory: ConsultingSubcategory | '';
  subcategory_confidence: number | null;
  consulting_audience: ConsultingAudience | '';
  is_open: boolean | null;
  days_until_deadline: number | null;
  source_url: string;
}

export interface ContractAward {
  supplier_name: string;
  supplier_reference: string;
  supplier_address: string;
  supplier_country: string;
  supplier_website: string;
  supplier_website_source: string;
  currency: string;
  bid_price_opening: string | null;
  evaluated_price: string | null;
  contract_price: string | null;
  award_date: string | null;
  contract_duration: string;
  evaluated_bidders: { name?: string; country?: string }[];
}

export interface ProjectDocument {
  guid: string;
  title: string;
  doc_type: string;
  doc_date: string | null;
  language: string;
  pdf_url: string;
  text_url: string;
  page_url: string;
}

export interface EsrsSummary {
  title: string;
  report_no: string;
  date: string | null;
  pdf_url: string;
  page_url: string;
}

export interface ProjectProfile {
  project_id: string;
  source: string;
  name: string;
  country: string;
  status: string;
  lending_instrument: string;
  implementing_agency: string;
  /** World Bank staff accountable for the project (comma-separated names). */
  team_lead: string;
  sectors: string[];
  themes: string[];
  total_amount_display: string;
  total_amount_usd: string | null;
  commitment_amount_usd: string | null;
  board_approval_date: string | null;
  closing_date: string | null;
  documents_count: number;
  project_url: string;
  has_esrs: boolean;
  esrs: EsrsSummary | null;
  fetched_at: string | null;
  documents_fetched_at: string | null;
}

export interface ProjectDocuments extends ProjectProfile {
  documents: ProjectDocument[];
}

/** `/tenders/{id}/documents/` before the project has been mirrored. */
export interface PendingProjectDocuments {
  project: null;
  documents: [];
  pending?: boolean;
  /** True when this request is what queued the mirror; false means one is
   *  already in flight. Either way the panel should say "being fetched". */
  queued?: boolean;
  project_id?: string;
}

export interface TenderContact {
  name: string;
  organization: string;
  email: string;
  phone: string;
  address: string;
  country: string;
  web_url: string;
}

/** Which of the three sources a contact came from. Also its priority order. */
export type ContactTier = 'notice' | 'body' | 'bank';

/** What the notice said an address is for, when it said anything. */
export type ContactPurpose = 'submission' | 'enquiry' | 'tor' | '';

/**
 * One reachable person. The same shape serves all three tiers, so the fields a
 * tier cannot fill arrive empty rather than absent — `tier` on the enclosing
 * group, not the presence of a field, decides how a card renders.
 */
export interface NoticeContact {
  name: string;
  role: string;
  organization: string;
  email: string;
  /** Further addresses for the same person, published order. */
  alternate_emails: string[];
  phone: string;
  address: string;
  country: string;
  website: string;
  purpose: ContactPurpose;
  /** The borrower's own wording around the address, for the tooltip. */
  context: string;
  source: 'notice_fields' | 'notice_body' | 'project_feed' | 'project_esrs';
  /** Tier 2: this is the tier-1 contact reached at a different address. */
  same_as_primary: boolean;
  /** Tier 3: the address was seen published, not derived from the pattern. */
  email_confirmed: boolean;
  links: { url: string; kind: string }[];
  summary: string;
  /** Tier 3: id of this person's profile page, "" when none is stored. */
  profile_id: string;
}

export interface ContactGroup {
  tier: ContactTier;
  /** 1 = strongest source. The backend sends groups already in this order. */
  priority: number;
  contacts: NoticeContact[];
}

/** Everyone reachable about a notice — see `apps/tenders/contacts.py`. */
export interface NoticeContacts {
  groups: ContactGroup[];
  total: number;
}

/**
 * One World Bank team lead.
 *
 * Professional information only — job title, unit, duty station, a work
 * address and public professional URLs. Personal social accounts, messaging
 * handles and photographs are deliberately absent: these are named private
 * individuals, and assembling their personal presence on one page would be a
 * dossier however each piece was found. See `apps/tenders/team_leads.py`.
 */
export interface TeamLeadDetail {
  /** The folded name, hyphenated — e.g. `mohini-kak`. */
  id: string;
  name: string;
  title: string;
  unit: string;
  country_office: string;
  organization: string;
  work_email: string;
  email_source: 'pattern' | 'verified' | '';
  /** False when the address was derived from the Bank's staff pattern. */
  email_confirmed: boolean;
  email_confidence: number | null;
  profile_url: string;
  links: { url: string; kind: string }[];
  summary: string;
  /** The Bank's own author page for this person, when they have one. */
  bank_page_url: string;
  /** Biography as the Bank publishes it. */
  bio: string;
  /** Official portrait, referenced on the Bank's CDN — never re-hosted. */
  photo_url: string;
  checked_at: string | null;
  source: string;
  projects: {
    project_id: string;
    name: string;
    country: string;
    status: string;
    implementing_agency: string;
    total_amount_display: string;
    project_url: string;
  }[];
  stats: { projects: number; notices: number; open_notices: number };
  notices: {
    id: string;
    title: string;
    country: string;
    notice_type: string;
    category: string;
    deadline_date: string | null;
    is_open: boolean | null;
    project_id: string;
  }[];
}

/** A URL found in the notice body, labelled by the sentence that introduced it. */
export interface NoticeLink {
  url: string;
  kind: 'tor' | 'bidding' | 'other';
  /** The wording the borrower used, shown as the link's tooltip. */
  context: string;
}

/**
 * How to obtain the Terms of Reference. Neither upstream API carries it, so
 * this is read out of the notice text: usually a link, sometimes only an
 * address to request it from, sometimes just a promise that it exists.
 */
export interface TorInfo {
  links: NoticeLink[];
  email: string;
  mentioned: boolean;
}

export interface TenderDetail extends TenderListItem {
  notice_language: string;
  submission_date: string | null;
  contact: TenderContact;
  /** The same primary contact plus the two weaker tiers, priority-ordered. */
  contacts: NoticeContacts;
  category_rationale: string;
  category_updated_at: string | null;
  /** Present on Contract Award notices once the body has been parsed. */
  award: ContractAward | null;
  /** Parent project dashboard, when the project has been mirrored. */
  project: ProjectProfile | null;
  tor: TorInfo;
  /** Sanitised server-side; still re-parsed through an allow-list on render. */
  notice_text_sanitized: string;
  created_at: string;
  updated_at: string;
  last_synced_at: string;
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

export interface FacetValue {
  value: string;
  count: number;
  label?: string;
}

export interface CountryGroupFacet {
  value: string;
  label: string;
  description: string;
  countries: { name: string; flag?: string; note?: string; count: number }[];
}

export interface Facets {
  countries: FacetValue[];
  procurement_methods: FacetValue[];
  notice_types: FacetValue[];
  categories: FacetValue[];
  subcategories: FacetValue[];
  consulting_audiences: FacetValue[];
  country_groups: CountryGroupFacet[];
  total_notices: number;
  focus_notices: number;
}

export interface FocusStats {
  country_group: string;
  notice_types: string[];
  open_only: boolean;
  total: number;
  closing_today: number;
  classified: number;
  by_category: FacetValue[];
  countries: FacetValue[];
}

export interface SyncRunSummary {
  id: number;
  started_at: string;
  finished_at: string | null;
  duration_seconds: number | null;
  status: string;
  trigger: string;
  created_count: number;
  updated_count: number;
  unchanged_count: number;
  pages_fetched: number;
  pages_failed: number;
  upstream_total: number | null;
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

export interface Stats {
  total_notices: number;
  open_notices: number;
  countries: number;
  latest_notice_date: string | null;
  earliest_notice_date: string | null;
  last_synced_at: string | null;
  last_sync_run: SyncRunSummary | null;
  archive: ArchiveProgress;
  focus: FocusStats;
  data_source: {
    name: string;
    url: string;
    license: string;
    license_url: string;
  };
}

export interface TenderQuery {
  page?: number;
  page_size?: number;
  country?: string;
  country_group?: string;
  procurement_method?: string;
  notice_type?: string;
  category?: string;
  subcategory?: string;
  consulting_audience?: string;
  focus?: string;
  is_open?: string;
  search?: string;
  ordering?: string;
}

/** One supplier in the competitor roster, aggregated across its awards. */
export interface CompanyRow {
  name: string;
  country: string;
  website: string;
  website_source: string;
  wins: number;
  first_award: string | null;
  latest_award: string | null;
  /**
   * Sum of contract prices, **USD awards only** — prices come in many
   * currencies and adding them would be meaningless. `usd_awards` says how
   * many of `wins` the total actually covers.
   */
  total_usd: string | null;
  usd_awards: number;
}

export interface CompanyAward extends ContractAward {
  notice_id: string;
  notice_title: string;
  notice_country: string;
  notice_category: TenderCategory;
}

export interface CompanyDetail extends Omit<CompanyRow, 'total_usd'> {
  total_usd: string | null;
  by_category: FacetValue[];
  by_country: FacetValue[];
  awards: CompanyAward[];
}

export interface CompanyQuery {
  page?: number;
  page_size?: number;
  search?: string;
  country?: string;
  category?: string;
  ordering?: string;
}

/* -------------------------------------------------------------------------
   Finished contracts — who won, and who else was in the room.
   ------------------------------------------------------------------------- */

/** The role a company held in one award notice. The notice publishes these as
 *  three separate lists and the product keeps them separate: losing on price
 *  and being ruled non-responsive are different facts about a competitor. */
export type AwardRole = 'awardee' | 'evaluated' | 'rejected';

export interface AwardParticipant {
  name: string;
  country: string;
  role: AwardRole;
  /** Only ever set on the winner — enrichment looks up nobody else. */
  website: string;
  website_source: string;
  /** Present on rejected bidders when the notice published a reason. */
  reason?: string;
}

export interface AwardRow {
  notice_id: string;
  title: string;
  country: string;
  category: string;
  subcategory: string;
  project_name: string;
  /** The upstream page, so a claim about someone else's business can be checked. */
  source_url: string;
  award_date: string | null;
  currency: string;
  contract_price: string | null;
  contract_duration: string;
  participants: AwardParticipant[];
}

/**
 * Contracts awarded for work like the tender being read.
 *
 * Retrieved by meaning over the semantic index (D45), where this was a
 * category filter before. So a row here — and only here — carries two extra
 * fields the awards feed does not:
 *
 * `match_passage` is **the sentence the match was made on**, and it is not
 * optional decoration. A cosine similarity is not something the reader of a
 * row can check; the sentence is. Rendering `match_score` without
 * `match_passage` would put back exactly the unaccountable ranking D42
 * removed, so the two travel together or not at all.
 */
export interface SimilarAwardRow extends AwardRow {
  match_score: number;
  match_passage: string;
}

export interface SimilarAwards {
  count: number;
  results: SimilarAwardRow[];
}

export interface AwardQuery {
  page?: number;
  page_size?: number;
  search?: string;
  country?: string;
  country_group?: string;
  category?: string;
  subcategory?: string;
  role?: string;
}

/* -------------------------------------------------------------------------
   Compliance — what a tender requires, and whether one vendor meets it.
   ------------------------------------------------------------------------- */

/**
 * Three outcomes, not two.
 *
 * `unknown` means the vendor has not declared something the criterion needs.
 * It is **not** a failure and must never be rendered as one: telling a vendor
 * they are ineligible because *we* lack a number costs them a bid, while
 * asking for the number costs them thirty seconds. See DECISIONS.md D3.
 */
export type Verdict = 'satisfied' | 'failed' | 'unknown';

/**
 * The four states a whole assessment can be in.
 *
 * `unrated` means nothing was extracted for this notice, so no claim about the
 * vendor has been made at all — distinct from `eligible`, and the reason a
 * client must read this field before rendering `hard_eligibility_pass`.
 */
export type AssessmentStatus = 'eligible' | 'blocked' | 'incomplete' | 'unrated';

/** Which column of the qualification matrix a criterion is read from. */
export type AppliesTo = 'single' | 'jv_combined' | 'jv_each' | 'jv_at_least_one';

/**
 * Which extraction layer produced a requirement — free, cheap, or expensive.
 *
 * The numbering starts at 1 because it once started at 0: an L0 read standard
 * requirements out of the Procurement Regulations by procurement method. It was
 * dropped (D17) — a requirement nobody wrote into this tender is not a fact
 * about this tender — and the remaining names were left alone so a stored row
 * still means what it meant when it was written.
 */
export type ExtractionLayer = 'L1' | 'L2' | 'L3';

/**
 * Whether the quote was found verbatim in the source.
 *
 * `not_found` never arrives here — the backend withholds those rows and only
 * counts them (see `AssessmentExcluded`). Every layer now reads a document we
 * hold, so every row can be checked against the text it came from and there is
 * no state meaning "grounding does not apply".
 */
export type Grounding = 'verified' | 'unchecked';

/** One step of the evaluation, and why it came out the way it did. */
export interface Trace {
  node: string;
  verdict: Verdict;
  detail: string;
  children: Trace[];
}

/**
 * A declaration that would settle an `unknown`.
 *
 * Populated only for `unknown` requirements. `collection` means a whole record
 * type was never declared; `record_field` means records exist but some are
 * blank in the field the criterion filters on.
 */
export interface MissingInput {
  kind: 'scalar' | 'collection' | 'record_field';
  key?: string;
  entity?: string;
  field?: string;
  label: string;
  unit?: string;
}

export interface RequirementSourceDocument {
  id: string;
  url: string;
  kind: string;
  status: string;
}

/** One criterion, its verdict, and enough working to recompute the verdict. */
/**
 * How much of the bid one criterion decides, as the extraction read it off the
 * document's own language: an eligibility gate is `high`, a stated preference
 * is `low`, everything the document requires without saying either is
 * `medium`. `''` is a criterion nobody judged — L1 reads none of the language
 * that would settle it — and is weighted and sorted as `medium`.
 *
 * It never reaches a verdict. It orders the list and weights the readiness
 * percentage, both of which summarise verdicts already reached.
 */
export type Importance = 'high' | 'medium' | 'low' | '';

export interface AssessedRequirement {
  id: number;
  key: string;
  /** Already in the reader's language: the server resolves it per request. */
  label: string;
  importance: Importance;
  verdict: Verdict;
  is_mandatory: boolean;
  applies_to: AppliesTo;
  /** Verbatim from the source. Never empty: a row without one is withheld. */
  evidence_quote: string;
  source: string;
  /** The requirement tree as stored — what was asked, beside what happened. */
  expression: Record<string, unknown>;
  trace: Trace;
  /** The trace flattened to indented text, produced deterministically. */
  explanation: string;
  missing: MissingInput[];
  layer: ExtractionLayer;
  grounding: Grounding;
  source_document: RequirementSourceDocument | null;
  run: { id: number; layers: string; model: string };
  /**
   * The vendor's own answer to this criterion, and therefore the switch's
   * position. `null` is "not answered" — distinct from `false`, which is a
   * statement the vendor has made about themselves.
   */
  declared: boolean | null;
  /**
   * Which side produced the verdict, so the page can say "because you told us"
   * rather than presenting a self-declaration as computed from evidence.
   */
  decided_by: 'declaration' | 'engine';
}

/**
 * Rows that existed but took no part.
 *
 * `not_found` is the hallucination signal: an extracted requirement whose
 * quote could not be located in the source. Shown as a count so a thin
 * assessment cannot pass for a complete one, never as content.
 */
export interface AssessmentExcluded {
  not_found: number;
  superseded: number;
  unparsable?: number;
}

export interface AssessmentNotice {
  id: string;
  title: string;
  country: string;
  notice_type: string;
  deadline_date: string | null;
  procurement_method_code: string;
  procurement_method_name: string;
}

/**
 * How ready this bid is, weighted by what each criterion decides.
 *
 * Two fractions rather than one, and the pair is the point. `score` is what has
 * been *established* — only a satisfied criterion fills it, never an answered
 * one. `ceiling` is what the vendor would reach by settling everything still
 * unknown. The gap between them is the work left; the gap from `ceiling` to 1
 * is what has already been lost and cannot be recovered by answering more.
 *
 * `blocked` is read before either of them. A bid can be 85% established and
 * impossible, and a bar that could only report the percentage would be
 * congratulating a bidder who cannot bid.
 */
export interface ComplianceScore {
  /** 0–1. Weight satisfied over total weight. */
  score: number;
  /** 0–1. What `score` becomes if every unknown is satisfied. */
  ceiling: number;
  /** The weights the fractions divide, so a reader can check the division. */
  earned: number;
  open: number;
  lost: number;
  total: number;
  counts: { total: number; satisfied: number; failed: number; unknown: number };
  /** Weight per level: which half of the tender is still missing. */
  by_importance: Record<string, { earned: number; total: number; count: number }>;
  /** A mandatory criterion has actually failed. */
  blocked: boolean;
}

export interface ComplianceAssessment {
  notice: AssessmentNotice;
  /** `id` is null when the profile was assessed without being stored. */
  profile: { id: number | null; name: string };
  status: AssessmentStatus;
  /**
   * The technical task's boolean, with its third state intact. `null` means a
   * mandatory requirement is still unknown — **never coerce it to `false`**.
   *
   * It is vacuously `true` when `status === 'unrated'` (an empty conjunction),
   * so read `status` first.
   */
  hard_eligibility_pass: boolean | null;
  /** Share of requirements that reached a definite verdict, 0–1. */
  coverage: number;
  score: ComplianceScore;
  explanation: string;
  counts: {
    total: number;
    satisfied: number;
    failed: number;
    unknown: number;
    /** Mandatory failures only — a failed preference does not block a bid. */
    blockers: number;
  };
  is_joint_venture: boolean;
  requirements: AssessedRequirement[];
  excluded: AssessmentExcluded;
  documents: NoticeDocumentState;
}

/** One indexed line of a mirrored PDF, with the rectangle it occupies. */
export interface SourceSpan {
  span_id: string;
  page: number;
  text: string;
  /** PDF points, origin at the **top left** — the browser's convention too. */
  bbox: { x0: number; top: number; x1: number; bottom: number };
  page_width: number;
  page_height: number;
}

/**
 * `/compliance/notices/{id}/document/` — the text the criteria were read out
 * of, and where each one's quote sits in it.
 *
 * The two location shapes are both present and exactly one is populated.
 * `highlights` maps a requirement id onto span ids and is filled only for an
 * indexed PDF; `ranges` maps it onto `[block, start, end]` triples and is
 * filled otherwise. Both are keyed by the requirement id **as a string**,
 * because JSON object keys always are.
 *
 * A requirement absent from both is one whose quote could not be located. That
 * is a normal state — the quote is still verified and still shown; only the
 * pointer is missing — and never an error.
 */
export interface NoticeSource {
  /** `notice_body`, `document`, or `''` when there is no source at all. */
  source: 'notice_body' | 'document' | '';
  document: {
    id: string;
    kind: string;
    url: string;
    origin: string;
    page_count: number | null;
    is_pdf: boolean;
  } | null;
  /**
   * The source as paragraphs, for the `text` mode. Empty when a PDF was
   * indexed. `tag` is what to render it as — the notice's own block structure,
   * so the pane keeps the line breaks the tender page has.
   */
  blocks: { tag: 'p' | 'h3' | 'h4' | 'li' | 'blockquote'; text: string }[];
  /** `[block index, start, end]` per block the quote covers. */
  ranges: Record<string, [number, number, number][]>;
  spans: SourceSpan[];
  highlights: Record<string, string[]>;
  /** Why there are no rectangles: `not_a_pdf`, `no_text_layer`, … */
  problem: string;
}

/** `/compliance/notices/{id}/requirements/` — the criteria, with no vendor. */
export interface NoticeRequirements {
  notice: AssessmentNotice;
  requirements: Omit<
    AssessedRequirement,
    'verdict' | 'trace' | 'explanation' | 'missing' | 'run'
  >[];
  excluded: AssessmentExcluded;
  documents: NoticeDocumentState;
}

/**
 * Who the notice says to write to for the tender document.
 *
 * Every field is a plain string because the source columns are, and an
 * undeclared contact arrives as `''` rather than as `null` — so render on
 * truthiness, not on presence.
 */
export interface NoticeContact {
  name: string;
  organization: string;
  email: string;
  phone: string;
  address: string;
  web_url: string;
}

/**
 * Which documents back this notice, and who to ask when none do.
 *
 * `contact` is populated **only** when nothing readable is attached. That is a
 * privacy rule on the backend's side, not an oversight: sending vendors to ask
 * for a document we already hold would spread a borrower's address for nothing.
 * A client must therefore branch on `can_extract`, never on `contact != null`
 * alone.
 */
/** The four kinds a supplied document can be filed as. */
export type DocumentKind = 'tor' | 'bidding' | 'project_doc' | 'other';

/** One document already held for a notice. */
export interface HeldDocument {
  id: string;
  kind: DocumentKind;
  /** `harvested` — the notice linked it. `client_supplied` — a vendor sent it. */
  origin: 'harvested' | 'client_supplied';
  url: string;
  text_chars: number;
  page_count: number | null;
}

export interface NoticeDocumentState {
  readable: number;
  supplied_by_vendors: number;
  can_extract: boolean;
  /** Which documents, not only how many — the upload slots render from this. */
  held: HeldDocument[];
  contact: NoticeContact | null;
}

/**
 * What came back from handing the backend a document a vendor obtained.
 *
 * `document.readable` false is a normal outcome, not an error — a scanned TOR
 * arrives fine and has no text layer — which is why `problem` is a sentence
 * for the vendor rather than an error code. `extraction` is null when nothing
 * was read, so the criteria list simply stays as it was.
 */
export interface DocumentSubmission {
  document: {
    id: number | null;
    kind: string;
    readable: boolean;
    text_chars: number;
    problem: string;
  };
  extraction: {
    layers: string;
    status: string;
    requirements_found: number;
    error: string;
  } | null;
  requirements: NoticeRequirements['requirements'];
  excluded: AssessmentExcluded;
}

/**
 * What a vendor declares about itself.
 *
 * `scalars` are single values, `collections` are the records a count runs
 * over. An absent key means *not declared*, which is what produces `unknown`
 * rather than a failure — so a partly filled profile is a normal state here.
 */
export interface VendorProfile {
  id: number;
  name: string;
  country: string;
  scalars: Record<string, string | number | boolean>;
  collections: Record<string, Record<string, string | number | boolean>[]>;
  consented_at: string | null;
  created_at: string;
  updated_at: string;
}

export type VendorProfileInput = Pick<
  VendorProfile,
  'name' | 'country' | 'scalars' | 'collections'
>;

/**
 * Who is signed in, and what they have declared.
 *
 * `user` is `null` for a visitor — the ordinary state, not an error, which is
 * why `/auth/me/` answers 200 rather than 401. `profile` is never null for a
 * signed-in vendor: registration creates both in one transaction.
 */
export interface VendorSession {
  user: { id: number; email: string } | null;
  profile: VendorProfile | null;
}

/* -------------------------------------------------------------------------
   Expert directory
   -------------------------------------------------------------------------
   Two shapes that must not be confused, and the type system is one of the
   places that keeps them apart.

   A `TenderExpertPosition` is *extracted*: the tender's own text says the team
   must include this person, and the quote is attached. An `Expert` is
   *curated*: someone we know, filed under a role. Only the first is a claim
   about a tender; the second is a suggestion about who might fill it.
   ------------------------------------------------------------------------- */

/** One role in the directory's taxonomy, with the family above it. */
export interface ExpertRole {
  slug: string;
  name: string;
  family: string;
  family_name: string;
  /** How many people the directory holds here. Zero is a gap worth showing. */
  expert_count?: number;
}

/** A top-level family and the roles under it. The taxonomy is two deep. */
export interface ExpertFamily {
  slug: string;
  name: string;
  roles: ExpertRole[];
}

/**
 * One person in the directory.
 *
 * A name, a public profile link, and the roles they work as — nothing else is
 * stored, so nothing else can be rendered.
 */
export interface Expert {
  id: number;
  full_name: string;
  linkedin_url: string;
  roles: ExpertRole[];
}

export interface ExpertQuery {
  role?: string[];
  family?: string;
  search?: string;
  ordering?: string;
  page?: number;
  page_size?: number;
}

/**
 * An expert position one tender asks for.
 *
 * `role` is `null` when the taxonomy has no row for what the tender named. The
 * position is still real and still shown — `title` says who is wanted — there
 * is simply nobody to suggest for it.
 */
export interface TenderExpertPosition {
  id: number;
  title: string;
  role: string | null;
  role_name: string;
  family: string;
  count: number;
  is_mandatory: boolean;
  layer: ExtractionLayer;
  evidence_quote: string;
  source: string;
  grounding: Grounding;
}

/**
 * The team a tender names, and who we hold for those roles.
 *
 * `candidates` is keyed by role slug, and a position whose `role` is `null`
 * contributes no key — which is why a client must render `positions` from
 * `positions`, and only look up `candidates` per position, never the reverse.
 */
export interface NoticeExperts {
  notice: AssessmentNotice;
  positions: TenderExpertPosition[];
  excluded: AssessmentExcluded;
  candidates: Record<string, Expert[]>;
}

/* -------------------------------------------------------------------------- */
/* Semantic search (/api/v1/)                                                  */
/* -------------------------------------------------------------------------- */

/**
 * Where a retrieved passage sits in the source it came from.
 *
 * Two disjoint shapes under one discriminator, mirroring the server's payload
 * exactly. `source_type` is the only safe way to read it: a text hit has no
 * `page`, a PDF hit has no `char_start`, and the keys are *absent* rather than
 * null so a component that forgets the switch fails at the type level instead
 * of rendering page `undefined`.
 *
 * `bbox` is `[x0, top, x1, bottom]` in PDF points with the origin at the top
 * left — pdfplumber's convention, and the browser's. The one coordinate flip
 * in the system stays on the server.
 */
export interface CitationPayload {
  source_key: string;
  notice_id: string;
  category: string;
  subcategory: string;
  document_id: string;
  title: string;
  /**
   * `record` is not a document: it is a row of our own tender database,
   * offered so a question about *which tenders are open* can be cited rather
   * than answered from a passage that does not say it. There is nothing to
   * open, so the UI links to the tender instead of the viewer.
   */
  source_type: 'pdf' | 'text' | 'record';
  position_id: string;

  /** PDF only. */
  page?: number;
  bbox?: [number, number, number, number];
  page_width?: number;
  page_height?: number;

  /** Text only. Offsets into the source's *canonical* form — see the viewer. */
  char_start?: number;
  char_end?: number;
  sentence_index?: number;
}

export interface SearchResult {
  /**
   * Cosine similarity for a vector hit, term coverage for a full-text one.
   *
   * **The two are not comparable**, which is why `retrieval` sits beside it.
   * Nothing in the UI may sort, average or threshold across both.
   */
  score: number;
  retrieval: 'vector' | 'fts' | 'record';
  content: string;
  notice_id: string;
  title: string;
  source_type: 'pdf' | 'text' | 'record';
  payload: CitationPayload;
}

export interface SearchResponse {
  retrieval: 'vector' | 'fts' | 'none';
  took_ms: number;
  /**
   * Why the vector path did not answer, when it did not — a stable code, so
   * the UI can localise it. Empty on the happy path.
   */
  degraded_reason: string;
  count: number;
  results: SearchResult[];
}

/** The exact string a text citation's offsets index. See `fetchSourceText`. */
export interface SourceText {
  source_key: string;
  source_type: 'pdf' | 'text';
  document_id: string;
  title: string;
  text: string;
  /**
   * Where the source's own paragraphs sit inside `text`.
   *
   * `text` is the canonical string the quote's offsets index, and
   * canonicalisation turns every paragraph break into a full stop — which is
   * right for quoting and unreadable as a page. These spans put the breaks
   * back where the borrower had them, located in the same string rather than
   * reconstructed, so a paragraph boundary and a highlight can never disagree.
   * Empty for a source with no markup (an extracted PDF has no paragraphs to
   * find, and guessing at them would draw lines the document does not have).
   */
  blocks?: { tag: string; start: number; end: number }[];
}

/**
 * One sentence of a chat answer, and the passages that support it.
 *
 * `sources` are **indices into `ChatAnswer.sources`**, validated on the server
 * against the passages actually retrieved — a model cannot cite a document it
 * was not shown, and an index that did not survive validation took its whole
 * claim with it. So a claim on screen always has at least one real passage
 * behind it, and the badge can open it without checking anything first.
 */
export interface ChatClaim {
  text: string;
  sources: number[];
}

export interface ChatAnswer {
  claims: ChatClaim[];
  /** The retrieved passages, in the order the claims' indices refer to. */
  sources: SearchResult[];
  retrieval: 'vector' | 'fts' | 'none';
  degraded_reason: string;
  /** Claims the model produced that cited nothing valid, and were removed. */
  unsupported: number;
  took_ms: number;
  prompt_version: string;
  /** The thread this answer belongs to. Present from the first answer on. */
  conversation_id?: string;
  message_id?: number | null;
  /** The thread's title, which the first question names. */
  title?: string;
}

/**
 * A stage of the pipeline, reported while it runs.
 *
 * These are the server's own stages rather than a client-side timer: `reading`
 * carries the number of passages that were actually selected, so the waiting
 * state says something true even when a stage is slow. A stage this client
 * does not recognise is rendered as the generic "working" state — the set can
 * grow server-side without a frontend release.
 */
export interface ChatStage {
  stage: 'retrieving' | 'reading' | 'writing' | 'degraded' | 'empty' | string;
  sources?: number;
  reason?: string;
}

/**
 * The sentence being written, as far as the model has written it.
 *
 * Explicitly not a `ChatClaim`: a draft carries no citations, because the
 * numbers are not written yet. It is replaced by the checked claim at the same
 * index the moment that claim closes, so nothing uncited is ever left standing
 * as an answer.
 */
export interface ChatDraft {
  index: number;
  text: string;
}

/** A claim as it arrives on the stream, with its position in the answer. */
export interface StreamedClaim extends ChatClaim {
  index: number;
}

/** A saved thread, as the sidebar lists it. */
export interface Conversation {
  id: string;
  title: string;
  notice_id: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

/**
 * One stored turn.
 *
 * An assistant turn carries the sources it was written from, not a reference
 * to them: the citation badge on a month-old answer opens the same passage it
 * opened when the answer was written, even if the index has been rebuilt since.
 */
export interface ConversationMessage {
  id: number;
  role: 'user' | 'assistant';
  text: string;
  claims: ChatClaim[];
  sources: SearchResult[];
  retrieval: 'vector' | 'fts' | 'none' | '';
  degraded_reason: string;
  unsupported: number;
  took_ms: number;
  prompt_version: string;
  created_at: string;
}

