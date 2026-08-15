import { useMemo, useState } from 'react';

import {
  fetchDocuments,
  fetchProject,
  fetchProjects,
  fetchRequirements,
} from '../api/client';
import type {
  AdminDocument,
  AdminProject,
  AdminProjectDetail,
  AdminProjectNotice,
  AdminRequirement,
  Paginated,
} from '../api/types';
import DataTable, { type Column } from '../components/DataTable';
import { Panel, StatusPill } from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useDocumentTitle, useI18n } from '../i18n';

/**
 * Project → notice → document → requirement, top down.
 *
 * The console had every level except a way in from above: an operator could
 * search notices and search requirements, but could not answer "what is this
 * project, what did it publish, and which of those documents did we actually
 * read" without already knowing an id. That is the state the World Bank's own
 * search leaves you in, and improving on it is the product.
 *
 * One page with a breadcrumb rather than three routes. The levels are only
 * meaningful in relation to each other — a document matters because of the
 * notice that links it, a requirement because of the document it was read out
 * of — and separate screens would make the operator carry ids between them by
 * hand, which is the thing being fixed.
 */
type Level = 'projects' | 'project' | 'notice';

export default function ExplorerPage() {
  const [searchInput, setSearchInput] = useState('');
  const search = useDebouncedValue(searchInput);
  const [scope, setScope] = useState<'focus' | 'all'>('focus');
  const [projectId, setProjectId] = useState<string | null>(null);
  const [noticeId, setNoticeId] = useState<string | null>(null);
  const { t, formatDate, formatNumber } = useI18n();
  useDocumentTitle('title.explorer');

  const level: Level = noticeId ? 'notice' : projectId ? 'project' : 'projects';

  const projects = useAsyncData<AdminProject[]>(
    (signal) => fetchProjects({ search, focus: scope }, signal),
    [search, scope],
  );

  const project = useAsyncData<AdminProjectDetail | null>(
    (signal) => (projectId ? fetchProject(projectId, signal) : Promise.resolve(null)),
    [projectId],
  );

  // Documents are fetched for whichever level is open: every document of the
  // project while browsing it, and the notice's own once one is selected.
  const documents = useAsyncData<Paginated<AdminDocument> | null>(
    (signal) =>
      noticeId
        ? fetchDocuments({ notice_id: noticeId, page_size: 50 }, signal)
        : projectId
          ? fetchDocuments({ project_id: projectId, page_size: 50 }, signal)
          : Promise.resolve(null),
    [projectId, noticeId],
  );

  const requirements = useAsyncData<Paginated<AdminRequirement> | null>(
    (signal) =>
      noticeId
        ? fetchRequirements({ search: noticeId, page_size: 100 }, signal)
        : Promise.resolve(null),
    [noticeId],
  );

  const projectColumns = useMemo<Column<AdminProject>[]>(
    () => [
      {
        key: 'project_id',
        header: t('explorer.col.project'),
        width: '120px',
        render: (row) => <code>{row.project_id}</code>,
      },
      {
        key: 'project_name',
        header: t('explorer.col.projectName'),
        render: (row) => row.project_name || '—',
      },
      { key: 'country', header: t('explorer.col.country'), width: '140px' },
      {
        key: 'notices',
        header: t('explorer.col.notices'),
        width: '90px',
        align: 'right',
        render: (row) => formatNumber(row.notices),
      },
      {
        key: 'documents',
        header: t('explorer.col.documents'),
        width: '90px',
        align: 'right',
        render: (row) => formatNumber(row.documents),
      },
      {
        key: 'requirements',
        header: t('explorer.col.requirements'),
        width: '110px',
        align: 'right',
        render: (row) => formatNumber(row.requirements),
      },
    ],
    [t, formatNumber],
  );

  const noticeColumns = useMemo<Column<AdminProjectNotice>[]>(
    () => [
      {
        key: 'notice_id',
        header: t('explorer.col.notice'),
        width: '130px',
        render: (row) => <code>{row.notice_id}</code>,
      },
      {
        key: 'bid_description',
        header: t('explorer.col.description'),
        render: (row) => row.bid_description || '—',
      },
      {
        key: 'notice_type',
        header: t('explorer.col.type'),
        width: '190px',
      },
      {
        key: 'deadline_date',
        header: t('explorer.col.deadline'),
        width: '150px',
        render: (row) => (
          <span>
            {formatDate(row.deadline_date)}
            {row.is_open === false && (
              <span className="requirement-optional"> · {t('explorer.closed')}</span>
            )}
          </span>
        ),
      },
      {
        key: 'documents',
        header: t('explorer.col.documents'),
        width: '90px',
        align: 'right',
        render: (row) => formatNumber(row.documents),
      },
      {
        key: 'requirements',
        header: t('explorer.col.requirements'),
        width: '110px',
        align: 'right',
        render: (row) => formatNumber(row.requirements),
      },
    ],
    [t, formatDate, formatNumber],
  );

  const documentColumns = useMemo<Column<AdminDocument>[]>(
    () => [
      {
        key: 'kind',
        header: t('explorer.col.kind'),
        width: '110px',
        render: (row) => <code>{row.kind}</code>,
      },
      {
        key: 'url',
        header: t('explorer.col.document'),
        render: (row) => (
          <div className="requirement-cell">
            <a
              href={row.url}
              target="_blank"
              rel="noreferrer noopener"
              className="document-link"
              title={row.url}
            >
              {row.url.split('/').pop() || row.url}
            </a>
            {row.link_context && (
              <span className="requirement-expression">{row.link_context}</span>
            )}
          </div>
        ),
      },
      {
        key: 'notice_ids',
        header: t('explorer.col.belongsTo'),
        width: '160px',
        render: (row) => (
          <div className="requirement-cell">
            {/* The whole reason this screen exists: a document stored once per
                URL is linked by every notice that pointed at it. */}
            <span className="requirement-expression">{row.notice_ids.join(', ') || '—'}</span>
            <span className="requirement-expression">{row.project_ids.join(', ')}</span>
          </div>
        ),
      },
      {
        key: 'status',
        header: t('explorer.col.status'),
        width: '130px',
        render: (row) => <StatusPill status={row.status} />,
      },
      {
        key: 'text_chars',
        header: t('explorer.col.chars'),
        width: '100px',
        align: 'right',
        render: (row) => (row.text_chars ? formatNumber(row.text_chars) : '—'),
      },
      {
        key: 'requirements',
        header: t('explorer.col.requirements'),
        width: '110px',
        align: 'right',
        render: (row) => formatNumber(row.requirements),
      },
    ],
    [t, formatNumber],
  );

  const requirementColumns = useMemo<Column<AdminRequirement>[]>(
    () => [
      {
        key: 'label',
        header: t('requirements.col.requirement'),
        render: (row) => (
          <div className="requirement-cell">
            <span className="requirement-statement">{row.label || row.key}</span>
            <span
              className={row.summary ? 'requirement-expression' : 'requirement-broken'}
            >
              {row.summary || t('requirements.unreadable')}
            </span>
          </div>
        ),
      },
      {
        key: 'layer',
        header: t('requirements.col.layer'),
        width: '80px',
        render: (row) => <code>{row.layer}</code>,
      },
      {
        key: 'grounding',
        header: t('requirements.col.grounding'),
        width: '130px',
        render: (row) => <StatusPill status={row.grounding} />,
      },
      {
        key: 'evidence_quote',
        header: t('requirements.col.quote'),
        render: (row) =>
          row.evidence_quote ? (
            <blockquote className="requirement-quote">{row.evidence_quote}</blockquote>
          ) : (
            <span className="requirement-broken">{t('requirements.noQuote')}</span>
          ),
      },
    ],
    [t],
  );

  const openNotice = project.data?.notices.find((n) => n.notice_id === noticeId);

  return (
    <>
      <header className="page-head">
        <h1>{t('explorer.heading')}</h1>
        <p>{t('explorer.subtitle')}</p>
      </header>

      <nav className="explorer-crumbs" aria-label={t('explorer.breadcrumb')}>
        <button
          type="button"
          className="crumb"
          onClick={() => {
            setProjectId(null);
            setNoticeId(null);
          }}
          disabled={level === 'projects'}
        >
          {t('explorer.allProjects')}
        </button>
        {projectId && (
          <>
            <span className="crumb-sep">›</span>
            <button
              type="button"
              className="crumb"
              onClick={() => setNoticeId(null)}
              disabled={level === 'project'}
            >
              {projectId}
            </button>
          </>
        )}
        {noticeId && (
          <>
            <span className="crumb-sep">›</span>
            <span className="crumb is-current">{noticeId}</span>
          </>
        )}
      </nav>

      {level === 'projects' && (
        <Panel
          title={t('explorer.projectsTitle')}
          description={
            projects.data ? t('explorer.projectCount', { count: projects.data.length }) : undefined
          }
          actions={
            <div className="panel-filters">
              <input
                type="search"
                placeholder={t('explorer.searchPlaceholder')}
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
              />
              <select
                value={scope}
                aria-label={t('explorer.filter.scope')}
                onChange={(event) => setScope(event.target.value as 'focus' | 'all')}
              >
                <option value="focus">{t('explorer.scope.focus')}</option>
                <option value="all">{t('explorer.scope.all')}</option>
              </select>
            </div>
          }
        >
          <DataTable
            columns={projectColumns}
            rows={projects.data ?? []}
            rowKey={(row) => row.project_id}
            loading={projects.loading}
            error={projects.error}
            emptyMessage={t('explorer.noProjects')}
            onRowClick={(row) => setProjectId(row.project_id)}
          />
        </Panel>
      )}

      {level === 'project' && (
        <>
          <Panel
            title={project.data?.project_name || projectId || ''}
            description={
              project.data
                ? `${project.data.country} · ${t('explorer.noticeCount', {
                    count: project.data.notices.length,
                  })}`
                : undefined
            }
          >
            <DataTable
              columns={noticeColumns}
              rows={project.data?.notices ?? []}
              rowKey={(row) => row.notice_id}
              loading={project.loading}
              error={project.error}
              emptyMessage={t('explorer.noNotices')}
              onRowClick={(row) => setNoticeId(row.notice_id)}
            />
          </Panel>

          <Panel
            title={t('explorer.projectDocuments')}
            description={t('explorer.projectDocumentsHint')}
          >
            <DataTable
              columns={documentColumns}
              rows={documents.data?.results ?? []}
              rowKey={(row) => row.id}
              loading={documents.loading}
              error={documents.error}
              emptyMessage={t('explorer.noDocuments')}
            />
          </Panel>
        </>
      )}

      {level === 'notice' && (
        <>
          <Panel
            title={openNotice?.bid_description || noticeId || ''}
            description={
              openNotice
                ? `${openNotice.notice_type} · ${t('explorer.deadlineIs')} ${formatDate(
                    openNotice.deadline_date,
                  )}`
                : undefined
            }
          >
            <dl className="notice-facts">
              <div>
                <dt>{t('explorer.col.notice')}</dt>
                <dd><code>{noticeId}</code></dd>
              </div>
              <div>
                <dt>{t('explorer.col.project')}</dt>
                <dd><code>{projectId}</code></dd>
              </div>
              <div>
                <dt>{t('explorer.col.status')}</dt>
                <dd>
                  {openNotice ? (
                    <StatusPill status={openNotice.notice_status} />
                  ) : (
                    '—'
                  )}
                </dd>
              </div>
              <div>
                <dt>{t('explorer.col.documents')}</dt>
                <dd>{formatNumber(documents.data?.count ?? 0)}</dd>
              </div>
              <div>
                <dt>{t('explorer.col.requirements')}</dt>
                <dd>{formatNumber(requirements.data?.count ?? 0)}</dd>
              </div>
            </dl>
          </Panel>

          <Panel
            title={t('explorer.noticeDocuments')}
            description={t('explorer.noticeDocumentsHint')}
          >
            <DataTable
              columns={documentColumns}
              rows={documents.data?.results ?? []}
              rowKey={(row) => row.id}
              loading={documents.loading}
              error={documents.error}
              emptyMessage={t('explorer.noDocuments')}
            />
          </Panel>

          <Panel
            title={t('explorer.noticeRequirements')}
            description={
              requirements.data
                ? t('requirements.recordCount', { count: requirements.data.count })
                : undefined
            }
          >
            <DataTable
              columns={requirementColumns}
              rows={requirements.data?.results ?? []}
              rowKey={(row) => row.id}
              loading={requirements.loading}
              error={requirements.error}
              emptyMessage={t('explorer.noRequirements')}
            />
          </Panel>
        </>
      )}
    </>
  );
}
