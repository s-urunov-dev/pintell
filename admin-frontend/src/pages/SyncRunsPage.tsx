import { useMemo, useState } from 'react';

import { fetchSyncRuns, triggerSync } from '../api/client';
import type { Paginated, SyncRun } from '../api/types';
import DataTable, { type Column } from '../components/DataTable';
import Pagination from '../components/Pagination';
import {
  ActionButton,
  type Feedback,
  FeedbackBanner,
  Panel,
  StatusPill,
} from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDocumentTitle, useI18n } from '../i18n';

const STATUSES = ['', 'success', 'partial', 'failed', 'running'];

export default function SyncRunsPage() {
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(1);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const { t, tStatus, formatDateTime, formatNumber } = useI18n();
  useDocumentTitle('title.syncRuns');

  const { data, loading, error, reload } = useAsyncData<Paginated<SyncRun>>(
    (signal) => fetchSyncRuns({ status: statusFilter, page, page_size: 25 }, signal),
    [statusFilter, page],
  );

  const columns = useMemo<Column<SyncRun>[]>(
    () => [
      {
        key: 'started_at',
        header: t('syncRuns.col.started'),
        render: (row) => formatDateTime(row.started_at),
        width: '190px',
      },
      {
        key: 'status',
        header: t('syncRuns.col.status'),
        render: (row) => <StatusPill status={row.status} />,
        width: '110px',
      },
      { key: 'trigger', header: t('syncRuns.col.trigger'), render: (row) => <code>{row.trigger}</code> },
      {
        key: 'pages',
        header: t('syncRuns.col.pages'),
        align: 'right',
        render: (row) => (
          <span className={row.pages_failed ? 'text-critical' : undefined}>
            {row.pages_fetched}/{row.pages_requested}
            {row.pages_failed
              ? ` ${t('syncRuns.pagesFailed', { count: row.pages_failed })}`
              : ''}
          </span>
        ),
      },
      {
        key: 'created_count',
        header: t('syncRuns.col.created'),
        align: 'right',
        render: (row) => formatNumber(row.created_count),
      },
      {
        key: 'updated_count',
        header: t('syncRuns.col.updated'),
        align: 'right',
        render: (row) => formatNumber(row.updated_count),
      },
      {
        key: 'unchanged_count',
        header: t('syncRuns.col.unchanged'),
        align: 'right',
        render: (row) => formatNumber(row.unchanged_count),
      },
      {
        key: 'duration_seconds',
        header: t('syncRuns.col.duration'),
        align: 'right',
        render: (row) =>
          row.duration_seconds != null
            ? t('syncRuns.durationSeconds', { count: row.duration_seconds })
            : '—',
      },
    ],
    [t],
  );

  const activeRun = data?.results.find((row) => row.id === expanded) ?? null;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t('syncRuns.heading')}</h1>
          <p className="muted small">{t('syncRuns.subtitle')}</p>
        </div>
        <div className="panel-actions">
          <ActionButton
            label={t('dashboard.runSync')}
            onRun={async () => {
              const result = await triggerSync({});
              return t('feedback.syncQueuedRefresh', { task: result.task_id.slice(0, 8) });
            }}
            onDone={(result) => {
              setFeedback(result);
              if (result?.tone === 'good') setTimeout(reload, 2000);
            }}
          />
          <button type="button" className="btn btn-ghost" onClick={reload}>
            {t('action.refresh')}
          </button>
        </div>
      </header>

      <FeedbackBanner feedback={feedback} />

      <LastRunSummary run={data?.results[0] ?? null} />

      <Panel
        title={t('syncRuns.historyTitle')}
        description={data ? t('syncRuns.recorded', { count: data.count }) : undefined}
        actions={
          <div className="segmented">
            {STATUSES.map((value) => (
              <button
                key={value || 'all'}
                type="button"
                className={statusFilter === value ? 'active' : ''}
                onClick={() => {
                  setStatusFilter(value);
                  setPage(1);
                }}
              >
                {value ? tStatus(value) : t('filter.all')}
              </button>
            ))}
          </div>
        }
      >
        <DataTable
          columns={columns}
          rows={data?.results ?? []}
          rowKey={(row) => row.id}
          loading={loading}
          error={error}
          emptyMessage={t('syncRuns.empty')}
          onRowClick={(row) => setExpanded(expanded === row.id ? null : row.id)}
          activeRowKey={expanded}
        />

        {activeRun && (
          <div className="drawer">
            <h3>{t('syncRuns.runNumber', { id: activeRun.id })}</h3>
            <dl className="kv-grid">
              <div>
                <dt>{t('syncRuns.finished')}</dt>
                <dd>{formatDateTime(activeRun.finished_at)}</dd>
              </div>
              <div>
                <dt>{t('syncRuns.noticesSeen')}</dt>
                <dd>{formatNumber(activeRun.notices_seen)}</dd>
              </div>
              <div>
                <dt>{t('syncRuns.skipped')}</dt>
                <dd>{formatNumber(activeRun.skipped_count)}</dd>
              </div>
              <div>
                <dt>{t('syncRuns.upstreamTotal')}</dt>
                <dd>{formatNumber(activeRun.upstream_total)}</dd>
              </div>
            </dl>
            {activeRun.error_message ? (
              <>
                <h4 className="section-title">{t('syncRuns.errors')}</h4>
                <pre className="code-block">{activeRun.error_message}</pre>
              </>
            ) : (
              <p className="muted small">{t('syncRuns.noErrors')}</p>
            )}
          </div>
        )}

        {/* The columns are counts of four different things and the names alone
            do not separate them — "updated" and "unchanged" in particular read
            as the same event. Spelled out once, under the table, rather than in
            a tooltip nobody hovers. */}
        <p className="muted small table-legend">{t('syncRuns.legend')}</p>

        {data && (
          <Pagination page={data.page} totalPages={data.total_pages} onChange={setPage} />
        )}
      </Panel>
    </>
  );
}

/**
 * The last run, in a sentence.
 *
 * The table below is the record; this is the answer to the question an
 * operator actually opens the page with — did the sync run, and did it find
 * anything. Reading that off eight numeric columns is work, and it is work
 * repeated every time somebody checks.
 */
function LastRunSummary({ run }: { run: SyncRun | null }) {
  const { t, formatNumber, formatDateTime } = useI18n();

  if (!run) return null;

  const failed = run.status === 'failed';
  const nothingNew = run.created_count === 0 && run.updated_count === 0;

  return (
    <div className={`last-run ${failed ? 'last-run-failed' : ''}`}>
      <div>
        <span className="last-run-label">{t('syncRuns.lastRun')}</span>{' '}
        <strong>{formatDateTime(run.started_at)}</strong>
        {run.duration_seconds != null && (
          <span className="muted">
            {' · '}
            {t('syncRuns.durationSeconds', { count: Math.round(run.duration_seconds) })}
          </span>
        )}
      </div>
      <p className="muted">
        {failed
          ? t('syncRuns.lastRunFailed', { error: run.error_message || '—' })
          : nothingNew
            ? t('syncRuns.lastRunNothing', { seen: formatNumber(run.notices_seen) })
            : t('syncRuns.lastRunFound', {
                created: formatNumber(run.created_count),
                updated: formatNumber(run.updated_count),
                seen: formatNumber(run.notices_seen),
              })}
      </p>
    </div>
  );
}
