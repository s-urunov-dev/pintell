import { useMemo, useState } from 'react';

import {
  fetchOverview,
  fetchPartitions,
  rescanPartitions,
  resetPartition,
  runPartition,
  triggerBackfill,
} from '../api/client';
import type { Overview, Paginated, Partition } from '../api/types';
import DataTable, { type Column } from '../components/DataTable';
import Pagination from '../components/Pagination';
import {
  ActionButton,
  type Feedback,
  FeedbackBanner,
  Panel,
  ProgressBar,
  StatCard,
  StatusPill,
} from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useDocumentTitle, useI18n } from '../i18n';

const STATUSES = ['', 'pending', 'running', 'completed', 'subdivided', 'failed'];

/** The upstream API refuses an offset beyond this; see `WORLDBANK.MAX_OFFSET`. */
const UPSTREAM_MAX_OFFSET = 100_000;

export default function BackfillPage() {
  const [statusFilter, setStatusFilter] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const search = useDebouncedValue(searchInput);
  const [page, setPage] = useState(1);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const { t, tStatus, formatDateTime, formatNumber } = useI18n();
  useDocumentTitle('title.backfill');

  const partitions = useAsyncData<Paginated<Partition>>(
    (signal) =>
      fetchPartitions(
        { status: statusFilter, search, page, page_size: 25, ordering: '-next_offset' },
        signal,
      ),
    [statusFilter, search, page, refreshToken],
  );
  const overview = useAsyncData<Overview>((signal) => fetchOverview(signal), [refreshToken]);

  const refreshAll = () => setRefreshToken((token) => token + 1);
  const handleDone = (result: Feedback) => {
    setFeedback(result);
    if (result?.tone === 'good') refreshAll();
  };

  const columns = useMemo<Column<Partition>[]>(
    () => [
      { key: 'key', header: t('backfill.col.partition'), render: (row) => <code>{row.key}</code> },
      {
        key: 'status',
        header: t('backfill.col.status'),
        width: '120px',
        render: (row) => <StatusPill status={row.status} />,
      },
      {
        key: 'progress',
        header: t('backfill.col.progress'),
        width: '180px',
        render: (row) => (
          <div className="cell-progress">
            <ProgressBar percent={row.progress_percent} />
            <span className="muted small">{formatNumber(row.progress_percent)}%</span>
          </div>
        ),
      },
      {
        key: 'offset',
        header: t('backfill.col.offset'),
        align: 'right',
        render: (row) =>
          `${formatNumber(row.next_offset)} / ${
            row.upstream_total === null ? '?' : formatNumber(row.upstream_total)
          }`,
      },
      {
        key: 'pages_done',
        header: t('backfill.col.pages'),
        align: 'right',
        render: (row) => (
          <span className={row.pages_failed ? 'text-critical' : undefined}>
            {formatNumber(row.pages_done)}
            {row.pages_failed
              ? ` ${t('syncRuns.pagesFailed', { count: row.pages_failed })}`
              : ''}
          </span>
        ),
      },
      {
        key: 'updated_at',
        header: t('backfill.col.updated'),
        render: (row) => formatDateTime(row.updated_at),
        width: '180px',
      },
      {
        key: 'actions',
        header: '',
        align: 'right',
        width: '170px',
        render: (row) => (
          <div className="row-actions">
            <ActionButton
              label={t('backfill.run')}
              variant="ghost"
              size="sm"
              onRun={async () => {
                const result = await runPartition(row.id);
                return t('feedback.sliceQueued', { partition: result.partition });
              }}
              onDone={handleDone}
            />
            <ActionButton
              label={t('backfill.reset')}
              variant="ghost"
              size="sm"
              confirm={t('backfill.confirmReset')}
              onRun={async () => {
                await resetPartition(row.id);
                return t('feedback.partitionReset', { partition: row.key });
              }}
              onDone={handleDone}
            />
          </div>
        ),
      },
    ],
    // Besides `t`, the row actions only call state setters, which are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t],
  );

  const archive = overview.data?.archive;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t('backfill.heading')}</h1>
          <p className="muted small">
            {t('backfill.subtitle', { max: UPSTREAM_MAX_OFFSET })}
          </p>
        </div>
        <div className="panel-actions">
          <ActionButton
            label={t('backfill.runNext')}
            onRun={async () => {
              const result = await triggerBackfill({});
              return t('feedback.backfillQueued', { partition: result.partition });
            }}
            onDone={handleDone}
          />
          <ActionButton
            label={t('backfill.rescan')}
            variant="ghost"
            onRun={async () => {
              const result = await rescanPartitions();
              return result.created
                ? t('feedback.partitionsCreated', {
                    count: result.created,
                    total: result.total,
                  })
                : t('feedback.noNewPartitions', { count: result.total });
            }}
            onDone={handleDone}
          />
          <button type="button" className="btn btn-ghost" onClick={refreshAll}>
            {t('action.refresh')}
          </button>
        </div>
      </header>

      <FeedbackBanner feedback={feedback} />

      {archive && (
        <>
          <div className="stat-row">
            <StatCard
              label={t('backfill.stat.partitionsDone')}
              value={`${archive.partitions_completed}/${archive.partitions_total}`}
            />
            <StatCard
              label={t('backfill.stat.coverage')}
              value={`${formatNumber(archive.percent)}%`}
            />
            <StatCard
              label={t('backfill.stat.stored')}
              value={formatNumber(archive.notices_stored)}
              hint={
                archive.upstream_total
                  ? t('backfill.stat.storedHint', { total: archive.upstream_total })
                  : undefined
              }
            />
            <StatCard
              label={t('backfill.stat.rowsWalked')}
              value={formatNumber(archive.rows_walked)}
            />
            <StatCard
              label={t('backfill.stat.state')}
              value={t(
                archive.complete
                  ? 'archiveState.complete'
                  : archive.enabled
                    ? 'archiveState.importing'
                    : 'archiveState.disabled',
              )}
              tone={archive.complete ? 'good' : archive.enabled ? 'neutral' : 'warning'}
            />
          </div>
          <ProgressBar percent={archive.percent} />
        </>
      )}

      <Panel
        title={t('backfill.partitionsTitle')}
        description={
          partitions.data
            ? t('backfill.partitionCount', { count: partitions.data.count })
            : undefined
        }
        actions={
          <div className="panel-filters">
            <input
              type="search"
              placeholder={t('backfill.filterPlaceholder')}
              value={searchInput}
              onChange={(event) => {
                setSearchInput(event.target.value);
                setPage(1);
              }}
            />
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
          </div>
        }
      >
        <DataTable
          columns={columns}
          rows={partitions.data?.results ?? []}
          rowKey={(row) => row.id}
          loading={partitions.loading}
          error={partitions.error}
          emptyMessage={t('backfill.empty')}
        />
        {partitions.data && (
          <Pagination
            page={partitions.data.page}
            totalPages={partitions.data.total_pages}
            onChange={setPage}
          />
        )}
      </Panel>
    </>
  );
}
