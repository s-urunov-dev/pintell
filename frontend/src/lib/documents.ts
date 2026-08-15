import type { ProjectDocument } from '../api/types';

/**
 * Order a project's documents by what a bidder actually needs.
 *
 * Two things make the raw list unusable if shown flat:
 *
 * 1. The feed (WDS) publishes *project* documents, not tender ones — there are
 *    no Terms of Reference or bidding documents in it at all. What it carries
 *    ranges from genuinely useful (the Procurement Plan lists what the project
 *    intends to buy) to pure administrative record (disbursement letters,
 *    meeting minutes).
 * 2. Living documents are republished on a schedule under the *same title*. One
 *    project here has 79 Procurement Plans that differ only by date. Only the
 *    newest is actionable; the rest are history.
 *
 * So documents come back in three groups: what to read, superseded revisions of
 * those, and the administrative remainder.
 *
 * Matching is on a normalised prefix of `doc_type` — upstream sometimes joins
 * several types with `;`, and the leading one is the primary classification.
 */

/** Types a bidder opens to understand the work. Order is the display order. */
const PRIMARY_TYPES = [
  'procurement plan',
  'project appraisal document',
  'project information document',
  'environmental and social review summary',
  'environmental and social management plan',
  'environmental and social management framework',
  'environmental and social commitment plan',
  'project paper',
  'stakeholder engagement plan',
];

function normalizeType(docType: string): string {
  return (docType || '').split(';')[0].trim().toLowerCase();
}

/** Rank within the primary group; `Infinity` means it is not primary. */
function primaryRank(document: ProjectDocument): number {
  const index = PRIMARY_TYPES.indexOf(normalizeType(document.doc_type));
  return index === -1 ? Number.POSITIVE_INFINITY : index;
}

/**
 * Identity of a *document*, as opposed to one of its revisions.
 *
 * Upstream reuses the exact title across republications, so title plus type is
 * a reliable key. Whitespace and case vary between revisions, hence the
 * normalisation.
 */
function revisionKey(document: ProjectDocument): string {
  const title = (document.title || '').trim().toLowerCase().replace(/\s+/g, ' ');
  return `${normalizeType(document.doc_type)}::${title}`;
}

export interface GroupedDocuments {
  /** The current version of each document worth reading. */
  primary: ProjectDocument[];
  /** Older republications of the documents in `primary`. */
  revisions: ProjectDocument[];
  /** Administrative record — agreements, letters, minutes, audit reports. */
  secondary: ProjectDocument[];
}

/** Newest first. Undated documents sort last rather than jumping the queue. */
function byDateDesc(a: ProjectDocument, b: ProjectDocument): number {
  return (b.doc_date ?? '').localeCompare(a.doc_date ?? '');
}

export function groupDocuments(documents: ProjectDocument[]): GroupedDocuments {
  const primaryCandidates: ProjectDocument[] = [];
  const secondary: ProjectDocument[] = [];

  for (const document of documents) {
    (primaryRank(document) === Number.POSITIVE_INFINITY
      ? secondary
      : primaryCandidates
    ).push(document);
  }

  // Newest first, so the first sighting of each key is the current version.
  primaryCandidates.sort(byDateDesc);

  const seen = new Set<string>();
  const primary: ProjectDocument[] = [];
  const revisions: ProjectDocument[] = [];

  for (const document of primaryCandidates) {
    const key = revisionKey(document);
    if (seen.has(key)) {
      revisions.push(document);
    } else {
      seen.add(key);
      primary.push(document);
    }
  }

  primary.sort((a, b) => primaryRank(a) - primaryRank(b) || byDateDesc(a, b));
  secondary.sort(byDateDesc);

  return { primary, revisions, secondary };
}
