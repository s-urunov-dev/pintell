import { useEffect, useRef } from 'react';

import { fetchIndexStatus } from '../api/client';
import type { IndexStatus } from '../api/types';
import { BootScreen, Panel, ProgressBar, StatCard } from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDocumentTitle, useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

/**
 * The semantic index, as two independent sets of numbers side by side.
 *
 * Every other console screen reports on something this deployment owns end to
 * end. This one reports on a *cache in another container*, and the whole design
 * follows from that: the numbers Qdrant gives about itself and the numbers
 * Postgres gives about what was sent to it are shown separately and never
 * reconciled into one "index size". A single merged figure would be right on
 * the happy path and would hide the two states that actually need an operator:
 * an import still landing (ours ahead, harmlessly) and a collection that was
 * dropped while the bookkeeping survived (ours ahead, permanently, with search
 * quietly falling back to Postgres the whole time).
 *
 * There is no button on this page, deliberately. The archive import is hours
 * of metered embedding calls over tens of thousands of sources, and a control
 * that starts it from a browser tab is a control someone clicks twice. It runs
 * from a shell, where the operator can see it, cost it with `--dry-run` first,
 * and interrupt it — the command is resumable precisely so that is safe.
 */
const REFRESH_MS = 15_000;

export default function IndexPage() {
  const { t, formatDateTime, formatNumber } = useI18n();
  useDocumentTitle('title.index');

  const { data, loading, error, reload } = useAsyncData<IndexStatus>(
    (signal) => fetchIndexStatus(signal),
    [],
  );

  // Polled rather than pushed: the numbers move only while an import is
  // running, and an import is started from a shell, not from here.
  useAutoRefresh(reload, REFRESH_MS);

  if (loading && !data) return <BootScreen message={t('index.loading')} />;
  if (error && !data) return <p className="error-banner">{errorMessage(error, t)}</p>;
  if (!data) return null;

  const { collection, archive } = data;
  const sourcesTotal = archive.sources_total ?? 0;
  const sourcesDone = (archive.notices_indexed ?? 0) + (archive.documents_indexed ?? 0);
  const coverage = sourcesTotal > 0 ? (sourcesDone / sourcesTotal) * 100 : 0;

  // Ours minus theirs. Positive is normal during an import and suspicious when
  // nothing is running; see the component docstring.
  const drift = data.chunks_recorded - collection.points;

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{t('index.heading')}</h1>
          <p className="muted">{t('index.subtitle')}</p>
        </div>
      </header>

      {!data.enabled && (
        <p className="notice-banner">
          {t('index.disabled')} <code>RAG_ENABLED</code>
        </p>
      )}

      <div className="stat-grid">
        <StatCard
          label={t('index.stat.connection')}
          value={collection.connected ? t('index.connected') : t('index.disconnected')}
          tone={collection.connected ? 'good' : 'critical'}
          hint={collection.connected ? data.collection_name : collection.error}
        />
        <StatCard
          label={t('index.stat.points')}
          value={formatNumber(collection.points)}
          hint={t('index.stat.pointsHint')}
          tone={collection.exists ? 'neutral' : 'warning'}
        />
        <StatCard
          label={t('index.stat.indexedVectors')}
          value={formatNumber(collection.indexed_vectors)}
          // Below `points` during and shortly after an import. Said on the
          // card, because otherwise it reads as a fault every single time.
          hint={t('index.stat.indexedVectorsHint')}
        />
        <StatCard
          label={t('index.stat.recorded')}
          value={formatNumber(data.chunks_recorded)}
          hint={
            drift === 0
              ? t('index.stat.recordedMatch')
              // `chunks`, not `count`: a `count` param routes the lookup
              // through `Intl.PluralRules`, and this one is already formatted.
              : t('index.stat.recordedDrift', { chunks: formatNumber(Math.abs(drift)) })
          }
          tone={drift === 0 ? 'good' : 'warning'}
        />
      </div>

      <Panel title={t('index.archive.title')} description={t('index.archive.description')}>
        <ProgressBar percent={coverage} />
        <p className="muted small">
          {t('index.archive.coverage', {
            done: formatNumber(sourcesDone),
            total: formatNumber(sourcesTotal),
            percent: coverage.toFixed(1),
          })}
        </p>

        <dl className="detail-grid">
          <dt>{t('index.archive.notices')}</dt>
          <dd>
            {formatNumber(archive.notices_indexed ?? 0)} /{' '}
            {formatNumber(archive.notices_total ?? 0)}
          </dd>

          <dt>{t('index.archive.documents')}</dt>
          <dd>
            {formatNumber(archive.documents_indexed ?? 0)} /{' '}
            {formatNumber(archive.documents_total ?? 0)}
          </dd>

          <dt>{t('index.archive.failed')}</dt>
          <dd>{formatNumber(archive.failed ?? 0)}</dd>

          <dt>{t('index.archive.lastIndexed')}</dt>
          <dd>
            {data.last_indexed_at ? formatDateTime(data.last_indexed_at) : t('index.never')}
          </dd>
        </dl>
      </Panel>

      <Panel title={t('index.config.title')} description={t('index.config.description')}>
        <dl className="detail-grid">
          <dt>{t('index.config.collection')}</dt>
          <dd>
            <code>{data.collection_name}</code>
            {collection.exists ? '' : ` — ${t('index.config.notCreated')}`}
          </dd>

          <dt>{t('index.config.model')}</dt>
          <dd>
            <code>{data.embed_model}</code>
          </dd>

          <dt>{t('index.config.vector')}</dt>
          <dd>
            {collection.vector_size > 0
              ? `${collection.vector_size}d · ${collection.distance}`
              : '—'}
          </dd>

          <dt>{t('index.config.pipeline')}</dt>
          <dd>v{data.pipeline_version}</dd>
        </dl>

        {/* The one action this screen has, written out rather than wired to a
            button. See the component docstring. */}
        <p className="muted small">
          {t('index.config.runHint')} <code>manage.py archive_to_qdrant --dry-run</code>
        </p>
      </Panel>
    </div>
  );
}

/**
 * Re-run `callback` on an interval, cleanly across unmounts.
 *
 * Inline rather than in `hooks/`: it is a handful of lines used by one screen,
 * and the console's other pollers each own their own cadence for their own
 * reasons — a shared hook invites a shared interval, which is what makes every
 * screen refresh at whichever rate was tuned for the busiest one.
 *
 * The callback is held in a ref so the interval is not torn down and rebuilt on
 * every render: `reload` from `useAsyncData` is a new function each time, and
 * an effect depending on it directly would restart the timer continuously and
 * never actually fire.
 */
function useAutoRefresh(callback: () => void, ms: number): void {
  const saved = useRef(callback);
  saved.current = callback;

  useEffect(() => {
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [ms]);
}
