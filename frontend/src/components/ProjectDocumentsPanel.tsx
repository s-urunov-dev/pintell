import type { PendingProjectDocuments, ProjectDocument, ProjectDocuments } from '../api/types';
import { useI18n } from '../i18n';
import { groupDocuments } from '../lib/documents';
import { errorMessage } from '../lib/errors';

interface ProjectDocumentsPanelProps {
  projectId: string;
  data: ProjectDocuments | PendingProjectDocuments | null;
  loading: boolean;
  error: unknown;
}

function hasProject(
  data: ProjectDocuments | PendingProjectDocuments | null,
): data is ProjectDocuments {
  return Boolean(data && 'project_id' in data && (data as ProjectDocuments).name !== undefined);
}

/**
 * Everything published under the notice's parent Project ID: the project's own
 * dashboard figures, the ESRS summary, and every document as a titled PDF link.
 */
export default function ProjectDocumentsPanel({
  projectId,
  data,
  loading,
  error,
}: ProjectDocumentsPanelProps) {
  const { t, tv, formatDate, formatNumber } = useI18n();

  if (!projectId) return null;

  const heading = t('project.title', { id: projectId });

  if (loading) {
    return (
      <section className="card">
        <h2 className="section-title">{heading}</h2>
        <div className="skeleton skeleton-line" style={{ width: '60%' }} />
        <div className="skeleton skeleton-line" style={{ width: '85%', marginTop: 8 }} />
      </section>
    );
  }

  if (error) {
    return (
      <section className="card">
        <h2 className="section-title">{heading}</h2>
        <p className="muted small">
          {t('project.loadError', { error: errorMessage(error, t) })}
        </p>
      </section>
    );
  }

  if (!hasProject(data)) {
    return (
      <section className="card">
        <h2 className="section-title">{heading}</h2>
        <p className="muted small">{t('project.notMirrored')}</p>
      </section>
    );
  }

  const project = data;
  const grouped = groupDocuments(project.documents);

  return (
    <section className="card project-panel">
      <header className="project-head">
        <div>
          <h2 className="section-title">{t('project.title', { id: project.project_id })}</h2>
          <h3>{project.name || t('project.untitled')}</h3>
        </div>
        {project.project_url && (
          <a
            className="btn btn-ghost btn-sm"
            href={project.project_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            {t('project.page')}
          </a>
        )}
      </header>

      <dl className="kv-grid">
        {project.status && (
          <div>
            <dt>{t('project.status')}</dt>
            <dd>{tv('status', project.status)}</dd>
          </div>
        )}
        {project.total_amount_display && (
          <div>
            <dt>{t('project.totalCost')}</dt>
            <dd>USD {project.total_amount_display}</dd>
          </div>
        )}
        {project.commitment_amount_usd && (
          <div>
            <dt>{t('project.commitment')}</dt>
            <dd>USD {formatNumber(Number(project.commitment_amount_usd))}</dd>
          </div>
        )}
        {project.implementing_agency && (
          <div>
            <dt>{t('project.implementingAgency')}</dt>
            <dd>{project.implementing_agency}</dd>
          </div>
        )}
        {project.lending_instrument && (
          <div>
            <dt>{t('project.instrument')}</dt>
            <dd>{project.lending_instrument}</dd>
          </div>
        )}
        {project.closing_date && (
          <div>
            <dt>{t('project.closingDate')}</dt>
            <dd>{formatDate(project.closing_date)}</dd>
          </div>
        )}
      </dl>

      {project.sectors.length > 0 && (
        <div className="chip-row">
          {project.sectors.map((sector) => (
            <span key={sector} className="tag tag-quiet">
              {sector}
            </span>
          ))}
        </div>
      )}

      {/* The ESRS had a card of its own here, above the document list. It is
          gone on purpose: the Environmental and Social Review Summary is one of
          the project's published documents and is already in the list below, so
          the card was a second route to a file the reader could reach anyway —
          and by leading the panel it implied the ESRS was the document a bidder
          came for, which it is not. What the ESRS is genuinely good for is its
          contact block, and that is read out of the file itself and shown among
          the contacts (`apps.tenders.esrs`), not as a link to a PDF nobody
          asked to open. */}

      {/* The count is of what is actually listed here. Revisions and the
          administrative record carry their own counts on their disclosures,
          so a single total would promise more than this list shows. */}
      <h3 className="section-title">
        {t('project.documents')}{' '}
        <span className="muted">({formatNumber(grouped.primary.length)})</span>
      </h3>

      {project.documents.length === 0 ? (
        <p className="muted small">{t('project.noDocuments')}</p>
      ) : (
        <>
          <DocumentList documents={grouped.primary} />

          {/* Superseded republications of the documents above. Kept reachable
              — a bidder may need the plan that was current last quarter — but
              never in the way of the version that is current now. */}
          {grouped.revisions.length > 0 && (
            <details className="document-more">
              <summary>
                {t('project.olderRevisions', { count: grouped.revisions.length })}
              </summary>
              <RevisionList documents={grouped.revisions} />
            </details>
          )}

          {/* The administrative record is real but rarely what someone came
              for, so it stays one click away instead of burying the rest. */}
          {grouped.secondary.length > 0 && (
            <details className="document-more">
              <summary>
                {t('project.otherDocuments', { count: grouped.secondary.length })}
              </summary>
              <DocumentList documents={grouped.secondary} />
            </details>
          )}
        </>
      )}
    </section>
  );
}

/**
 * Older versions of the documents listed above.
 *
 * Rendered by **date**, not by title, and that is the whole point of the
 * component existing separately. The World Bank republishes a living document
 * — a Procurement Plan most of all — every time it changes, and gives every
 * version the identical title: one project here has 68 of them. Listed the
 * usual way, that is sixty-eight rows of the same sentence, and a reader can
 * only conclude the page is repeating itself or the links are broken. They are
 * neither: every row is a different file.
 *
 * So the date leads, the title is dropped (the group it sits in already names
 * it), and the eye has something that actually differs to move along.
 */
function RevisionList({ documents }: { documents: ProjectDocument[] }) {
  const { t, tv, formatDate } = useI18n();

  return (
    <ul className="document-list revision-list">
      {documents.map((document) => (
        <li key={document.guid}>
          <div className="document-main">
            <a
              href={document.page_url || document.pdf_url}
              target="_blank"
              rel="noopener noreferrer"
              className="document-title"
            >
              {formatDate(document.doc_date) || t('project.undatedRevision')}
            </a>
            <span className="muted small">
              {[tv('docType', document.doc_type), tv('docLanguage', document.language)]
                .filter((part) => part && part !== '\u2014')
                .join(' \u00b7 ')}
            </span>
          </div>
          {document.pdf_url && (
            <a
              className="btn btn-ghost btn-sm doc-download"
              href={document.pdf_url}
              target="_blank"
              rel="noopener noreferrer"
              download
              title={t('project.downloadPdf')}
            >
              <span aria-hidden="true">↓</span> PDF
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}

/** One group of documents: title, metadata, and a direct link to the PDF. */
function DocumentList({ documents }: { documents: ProjectDocument[] }) {
  const { t, tv, formatDate } = useI18n();

  return (
    <ul className="document-list">
      {documents.map((document) => (
        <li key={document.guid}>
          <div className="document-main">
            {/* The title goes to the document's page, the button to the file.
                They used to be the same URL — `pdf_url || page_url` — which
                put two controls side by side that did exactly one thing, and
                left no way at all to reach the record behind a document
                (its abstract, its other language editions, its revision it
                supersedes). Every mirrored row has a `page_url`, so the
                fallback is for a shape upstream has not shown us yet. */}
            <a
              href={document.page_url || document.pdf_url}
              target="_blank"
              rel="noopener noreferrer"
              className="document-title"
            >
              {document.title || t('project.untitledDocument')}
            </a>
            <span className="muted small">
              {[
                tv('docType', document.doc_type),
                formatDate(document.doc_date),
                tv('docLanguage', document.language),
              ]
                .filter((part) => part && part !== '\u2014')
                .join(' \u00b7 ')}
            </span>
          </div>
          {document.pdf_url && (
            <a
              className="btn btn-ghost btn-sm doc-download"
              href={document.pdf_url}
              target="_blank"
              rel="noopener noreferrer"
              /* Cross-origin, so the browser decides between saving and
                 opening its PDF viewer; either way the file is one click. */
              download
              title={t('project.downloadPdf')}
            >
              <span aria-hidden="true">↓</span> PDF
            </a>
          )}
        </li>
      ))}
    </ul>
  );
}
