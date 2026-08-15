import { useMemo, useState } from 'react';

import { fetchRequirementNotices, fetchRequirements } from '../api/client';
import type { AdminRequirement, Paginated, RequirementNotice } from '../api/types';
import DataTable, { type Column } from '../components/DataTable';
import Pagination from '../components/Pagination';
import { Panel, StatusPill } from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useDocumentTitle, useI18n } from '../i18n';

/**
 * What the extraction produced, row by row.
 *
 * The compliance screen answers "did it run" — 24 open tenders, 5 read, 2
 * requirements found. This screen answers the question after it: what does a
 * tender actually demand, and is the answer any good? Those are separate
 * screens because they are separate jobs. Watching a batch land is a glance at
 * counters; checking whether an extracted criterion is real means reading the
 * sentence next to the quote it came from.
 *
 * Two deliberate choices:
 *
 * - **The quote is a column, not a detail view.** Every row shows the text the
 *   requirement was read out of. Auditing means comparing the two, and a
 *   comparison that costs a click per row is a comparison nobody makes.
 * - **`grounding` is the first filter offered.** A requirement whose quote was
 *   not found in its source is the hallucination signal, and it is the reason
 *   an operator opens this page rather than the compliance one.
 */
export default function RequirementsPage() {
  const [searchInput, setSearchInput] = useState('');
  const search = useDebouncedValue(searchInput);
  const [layer, setLayer] = useState('');
  const [grounding, setGrounding] = useState('');
  const [noticeId, setNoticeId] = useState('');
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const { t, formatDate } = useI18n();
  useDocumentTitle('title.requirements');

  const requirements = useAsyncData<Paginated<AdminRequirement>>(
    (signal) =>
      fetchRequirements(
        {
          // The dropdown filters by tender through the same search the box
          // uses: the notice id is an exact token, so one parameter serves both
          // and there is no second filter for the backend to contradict.
          search: noticeId || search,
          layer,
          grounding,
          page,
          page_size: 25,
        },
        signal,
      ),
    [search, layer, grounding, noticeId, page],
  );

  const notices = useAsyncData<RequirementNotice[]>(
    (signal) => fetchRequirementNotices(signal),
    [],
  );

  const resetTo = (apply: () => void) => {
    apply();
    setPage(1);
    setExpandedId(null);
  };

  const columns = useMemo<Column<AdminRequirement>[]>(
    () => [
      {
        key: 'notice',
        header: t('requirements.col.tender'),
        width: '210px',
        render: (row) => (
          <div className="requirement-tender">
            <code>{row.notice_id}</code>
            <span className="requirement-tender-title" title={row.notice_title}>
              {row.notice_title || '—'}
            </span>
            <span className="requirement-tender-meta">
              {[row.notice_country, formatDate(row.notice_deadline)]
                .filter(Boolean)
                .join(' · ')}
            </span>
          </div>
        ),
      },
      {
        key: 'label',
        header: t('requirements.col.requirement'),
        render: (row) => (
          <div className="requirement-cell">
            {/* The label leads, because it is the requirement as a person would
                state it — "At least three completed oversight reform projects".
                The expression summary is the machine reading of the same thing
                and belongs underneath: it is what the verdict is computed from,
                which an operator needs second, not first. Leading with it was
                the reason this table read as gibberish. */}
            <span className="requirement-statement">
              {row.label || row.key}
            </span>
            <span
              className={
                row.summary ? 'requirement-expression' : 'requirement-broken'
              }
            >
              {row.summary || t('requirements.unreadable')}
            </span>
            {!row.is_mandatory && (
              <span className="requirement-optional">{t('requirements.optional')}</span>
            )}
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
        render: (row) => {
          const expanded = expandedId === row.id;
          if (!row.evidence_quote) {
            return <span className="requirement-broken">{t('requirements.noQuote')}</span>;
          }
          return (
            <blockquote
              className={expanded ? 'requirement-quote is-expanded' : 'requirement-quote'}
              title={expanded ? undefined : row.evidence_quote}
            >
              {row.evidence_quote}
            </blockquote>
          );
        },
      },
    ],
    [t, formatDate, expandedId],
  );

  return (
    <>
      <header className="page-head">
        <h1>{t('requirements.heading')}</h1>
        <p>{t('requirements.subtitle')}</p>
      </header>

      <Panel
        title={t('requirements.panelTitle')}
        description={
          requirements.data
            ? t('requirements.recordCount', { count: requirements.data.count })
            : undefined
        }
        actions={
          <div className="panel-filters">
            <input
              type="search"
              placeholder={t('requirements.searchPlaceholder')}
              value={searchInput}
              onChange={(event) => resetTo(() => setSearchInput(event.target.value))}
            />
            <select
              value={noticeId}
              aria-label={t('requirements.filter.tender')}
              onChange={(event) => resetTo(() => setNoticeId(event.target.value))}
            >
              <option value="">{t('requirements.filter.allTenders')}</option>
              {(notices.data ?? []).map((notice) => (
                <option key={notice.notice_id} value={notice.notice_id}>
                  {notice.notice_id} ({notice.requirements})
                </option>
              ))}
            </select>
            <select
              value={grounding}
              aria-label={t('requirements.filter.grounding')}
              onChange={(event) => resetTo(() => setGrounding(event.target.value))}
            >
              <option value="">{t('requirements.filter.allGrounding')}</option>
              <option value="verified">{t('requirements.grounding.verified')}</option>
              <option value="not_found">{t('requirements.grounding.notFound')}</option>
              <option value="unchecked">{t('requirements.grounding.unchecked')}</option>
            </select>
            <select
              value={layer}
              aria-label={t('requirements.filter.layer')}
              onChange={(event) => resetTo(() => setLayer(event.target.value))}
            >
              <option value="">{t('requirements.filter.allLayers')}</option>
              <option value="L1">L1</option>
              <option value="L2">L2</option>
              <option value="L3">L3</option>
            </select>
          </div>
        }
      >
        <DataTable
          columns={columns}
          rows={requirements.data?.results ?? []}
          rowKey={(row) => row.id}
          loading={requirements.loading}
          error={requirements.error}
          emptyMessage={t('requirements.empty')}
          onRowClick={(row) => setExpandedId(row.id === expandedId ? null : row.id)}
          activeRowKey={expandedId}
        />
        {requirements.data && (
          <Pagination
            page={requirements.data.page}
            totalPages={requirements.data.total_pages}
            onChange={setPage}
          />
        )}
      </Panel>
    </>
  );
}
