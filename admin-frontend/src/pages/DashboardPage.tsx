import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { fetchOverview, triggerBackfill, triggerEnrichment, triggerSync } from '../api/client';
import type { FacetCount, Overview } from '../api/types';
import YearBarChart from '../components/YearBarChart';
import {
  ActionButton,
  BootScreen,
  type Feedback,
  FeedbackBanner,
  Panel,
  ProgressBar,
  StatCard,
  StatusPill,
} from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDocumentTitle, useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

const AUTO_REFRESH_MS = 30_000;

/** Matches the window the API uses for the "closing soon" statistic. */
const CLOSING_SOON_DAYS = 7;

export default function DashboardPage() {
  const { data, loading, error, reload } = useAsyncData<Overview>(
    (signal) => fetchOverview(signal),
    [],
  );
  const [feedback, setFeedback] = useState<Feedback>(null);
  const { t, formatDate, formatDateTime, formatNumber } = useI18n();
  useDocumentTitle('title.dashboard');

  useEffect(() => {
    const timer = setInterval(reload, AUTO_REFRESH_MS);
    return () => clearInterval(timer);
  }, [reload]);

  if (loading && !data) return <BootScreen />;

  if (error != null && !data) {
    return (
      <div className="banner banner-critical" role="alert">
        {errorMessage(error, t)}{' '}
        <button type="button" className="btn btn-ghost btn-sm" onClick={reload}>
          {t('action.retry')}
        </button>
      </div>
    );
  }

  if (!data) return null;

  const { notices, freshness, sync_health: health, archive } = data;

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t('dashboard.heading')}</h1>
          <p className="muted small">
            {t('dashboard.updated', {
              when: formatDateTime(data.generated_at),
              seconds: AUTO_REFRESH_MS / 1000,
            })}
          </p>
        </div>
        <div className="panel-actions">
          <ActionButton
            label={t('dashboard.runSync')}
            variant="ghost"
            onRun={async () => {
              const result = await triggerSync({});
              return t('feedback.syncQueued', { task: result.task_id.slice(0, 8) });
            }}
            onDone={setFeedback}
          />
          <ActionButton
            label={t('dashboard.runBackfill')}
            variant="ghost"
            onRun={async () => {
              const result = await triggerBackfill({});
              return t('feedback.backfillQueued', { partition: result.partition });
            }}
            onDone={setFeedback}
          />
          <ActionButton
            label={t('dashboard.runEnrichment')}
            variant="ghost"
            onRun={async () => {
              const result = await triggerEnrichment({});
              return t('feedback.enrichmentQueued', { task: result.task_id.slice(0, 8) });
            }}
            onDone={setFeedback}
          />
          <button type="button" className="btn btn-ghost" onClick={reload}>
            {t('action.refresh')}
          </button>
        </div>
      </header>

      <FeedbackBanner feedback={feedback} />

      {freshness.stale && (
        <div className="banner banner-warning" role="status">
          {t('dashboard.stale', { count: freshness.minutes_since_sync })}{' '}
          {t('dashboard.staleCheck')}{' '}
          <Link to="/system">{t('shell.nav.system')}</Link>.
        </div>
      )}

      <div className="stat-row">
        <StatCard label={t('dashboard.stat.mirrored')} value={formatNumber(notices.total)} />
        <StatCard label={t('dashboard.stat.open')} value={formatNumber(notices.open)} />
        <StatCard
          label={t('dashboard.stat.closingSoon', { days: CLOSING_SOON_DAYS })}
          value={formatNumber(notices.closing_within_7_days)}
          tone={notices.closing_within_7_days > 0 ? 'warning' : 'neutral'}
        />
        <StatCard label={t('dashboard.stat.countries')} value={formatNumber(notices.countries)} />
        <StatCard
          label={t('dashboard.stat.archiveRange')}
          value={
            <span className="stat-range">
              {formatDate(notices.earliest_notice_date)} → {formatDate(notices.latest_notice_date)}
            </span>
          }
          hint={t('dashboard.stat.undated', { count: notices.without_notice_date })}
        />
      </div>

      <Panel
        title={t('dashboard.chartTitle')}
        description={t('dashboard.chartHint')}
      >
        <YearBarChart data={data.notices_per_year} />
      </Panel>

      <div className="grid-2">
        <Panel
          title={t('dashboard.archiveTitle')}
          description={t('dashboard.archiveHint', {
            done: archive.partitions_completed,
            total: archive.partitions_total,
          })}
          actions={
            <Link className="btn btn-ghost btn-sm" to="/backfill">
              {t('action.manage')}
            </Link>
          }
        >
          <ProgressBar percent={archive.percent} />
          <dl className="kv-grid">
            <div>
              <dt>{t('dashboard.storedLocally')}</dt>
              <dd>{formatNumber(archive.notices_stored)}</dd>
            </div>
            <div>
              <dt>{t('dashboard.upstreamTotal')}</dt>
              <dd>{formatNumber(archive.upstream_total)}</dd>
            </div>
            <div>
              <dt>{t('dashboard.rowsWalked')}</dt>
              <dd>{formatNumber(archive.rows_walked)}</dd>
            </div>
            <div>
              <dt>{t('dashboard.state')}</dt>
              <dd>
                {t(
                  archive.complete
                    ? 'archiveState.complete'
                    : archive.enabled
                      ? 'archiveState.importing'
                      : 'archiveState.disabled',
                )}
              </dd>
            </div>
          </dl>
        </Panel>

        <Panel
          title={t('dashboard.syncHealth')}
          description={t('dashboard.lastHours', { count: health.window_hours })}
          actions={
            <Link className="btn btn-ghost btn-sm" to="/sync-runs">
              {t('action.allRuns')}
            </Link>
          }
        >
          <dl className="kv-grid">
            <div>
              <dt>{t('dashboard.runs')}</dt>
              <dd>{formatNumber(health.runs_in_window)}</dd>
            </div>
            <div>
              <dt>{t('dashboard.failedPartial')}</dt>
              <dd className={health.failures_in_window ? 'text-critical' : undefined}>
                {formatNumber(health.failures_in_window)}
              </dd>
            </div>
            <div>
              <dt>{t('dashboard.lastRun')}</dt>
              <dd>
                {health.last_run_status ? <StatusPill status={health.last_run_status} /> : '—'}{' '}
                {formatDateTime(health.last_run_at)}
              </dd>
            </div>
            <div>
              <dt>{t('dashboard.lastSuccess')}</dt>
              <dd>{formatDateTime(health.last_success_at)}</dd>
            </div>
          </dl>
        </Panel>
      </div>

      <div className="grid-3">
        <Panel title={t('dashboard.topCountries')}>
          <FacetList rows={data.top_countries} total={notices.total} />
        </Panel>
        <Panel title={t('dashboard.methods')}>
          <FacetList rows={data.procurement_methods} total={notices.total} />
        </Panel>
        <Panel title={t('dashboard.noticeTypes')}>
          <FacetList rows={data.notice_types} total={notices.total} />
        </Panel>
      </div>
    </>
  );
}

function FacetList({ rows, total }: { rows: FacetCount[]; total: number }) {
  const { t, formatNumber } = useI18n();

  // Facet values are upstream vocabulary (country and method names as the
  // World Bank publishes them); only the empty state is translated.
  if (rows.length === 0) return <p className="muted small">{t('dashboard.noData')}</p>;

  return (
    <ul className="facet-list">
      {rows.map((row) => (
        <li key={row.value}>
          <span className="facet-label" title={row.value}>{row.value}</span>
          <span className="facet-bar" aria-hidden="true">
            <span
              className="facet-bar-fill"
              style={{ width: `${total ? Math.max(2, (row.count / total) * 100) : 0}%` }}
            />
          </span>
          <span className="facet-count">{formatNumber(row.count)}</span>
        </li>
      ))}
    </ul>
  );
}
