import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { fetchFacets, fetchStats, fetchTenders } from '../api/client';
import type { ArchiveProgress, Facets, Paginated, Stats, TenderListItem } from '../api/types';
import FilterPanel, { type FilterValues } from '../components/FilterPanel';
import Pagination from '../components/Pagination';
import { EmptyState, ErrorState, ListSkeleton } from '../components/StateViews';
import TenderCard from '../components/TenderCard';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';

const FILTER_KEYS: (keyof FilterValues)[] = [
  'country',
  'procurement_method',
  'notice_type',
  'category',
  'subcategory',
  'consulting_audience',
  'is_open',
  'search',
];

export default function TenderListPage() {
  // The URL is the single source of truth for filters, so any view is shareable.
  const [searchParams, setSearchParams] = useSearchParams();
  const { t, formatDate, formatNumber, formatPercent } = useI18n();

  const filters = useMemo<FilterValues>(
    () => ({
      country: searchParams.get('country') ?? '',
      procurement_method: searchParams.get('procurement_method') ?? '',
      notice_type: searchParams.get('notice_type') ?? '',
      category: searchParams.get('category') ?? '',
      subcategory: searchParams.get('subcategory') ?? '',
      consulting_audience: searchParams.get('consulting_audience') ?? '',
      // The focus feed is the default view; `?focus=false` opens the archive.
      focus: searchParams.get('focus') ?? '',
      is_open: searchParams.get('is_open') ?? '',
      search: searchParams.get('search') ?? '',
    }),
    [searchParams],
  );
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1);
  const focusOn = filters.focus !== 'false';

  const tenders = useAsyncData<Paginated<TenderListItem>>(
    (signal) =>
      fetchTenders(
        {
          country: filters.country,
          procurement_method: filters.procurement_method,
          category: filters.category,
          subcategory: filters.subcategory,
          consulting_audience: filters.consulting_audience,
          search: filters.search,
          // In focus mode the API applies the region, the notice types and the
          // open deadline; the type/status controls are hidden to match.
          ...(focusOn
            ? { focus: 'true' }
            : { notice_type: filters.notice_type, is_open: filters.is_open }),
          page,
        },
        signal,
      ),
    [
      filters.country,
      filters.procurement_method,
      filters.notice_type,
      filters.category,
      filters.subcategory,
      filters.consulting_audience,
      filters.focus,
      filters.is_open,
      filters.search,
      page,
    ],
  );
  const facets = useAsyncData<Facets>((signal) => fetchFacets(signal), []);
  const stats = useAsyncData<Stats>((signal) => fetchStats(signal), []);

  const updateFilters = useCallback(
    (patch: Partial<FilterValues>) => {
      const next = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      next.delete('page'); // any filter change resets to the first page
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const resetFilters = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    FILTER_KEYS.forEach((key) => next.delete(key));
    next.delete('page');
    // `focus` is deliberately preserved: clearing filters should not silently
    // move the user from the focus feed to the whole archive.
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const goToPage = useCallback(
    (nextPage: number) => {
      const next = new URLSearchParams(searchParams);
      if (nextPage <= 1) next.delete('page');
      else next.set('page', String(nextPage));
      setSearchParams(next);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    [searchParams, setSearchParams],
  );

  return (
    <>
      <section className="page-head">
        <div>
          <h1>{t(focusOn ? 'list.titleFocus' : 'list.titleArchive')}</h1>
          <p className="lead">{t(focusOn ? 'list.leadFocus' : 'list.leadArchive')}</p>
        </div>

        <div className="stat-row">
          {focusOn && stats.data ? (
            <>
              <StatTile
                label={t('list.stat.openOpportunities')}
                value={formatNumber(stats.data.focus.total)}
              />
              <StatTile
                label={t('list.stat.closingToday')}
                value={formatNumber(stats.data.focus.closing_today)}
              />
              <StatTile
                label={t('list.stat.countries')}
                value={formatNumber(stats.data.focus.countries.length)}
              />
              <StatTile
                label={t('list.stat.categorised')}
                value={
                  stats.data.focus.total
                    ? formatPercent(stats.data.focus.classified / stats.data.focus.total)
                    : '—'
                }
              />
              <StatTile
                label={t('list.stat.latestNotice')}
                value={formatDate(stats.data.latest_notice_date)}
              />
            </>
          ) : (
            <>
              <StatTile
                label={t('list.stat.noticesMirrored')}
                value={stats.data ? formatNumber(stats.data.total_notices) : '—'}
              />
              <StatTile
                label={t('list.stat.currentlyOpen')}
                value={stats.data ? formatNumber(stats.data.open_notices) : '—'}
              />
              <StatTile
                label={t('list.stat.countriesRegions')}
                value={stats.data ? formatNumber(stats.data.countries) : '—'}
              />
              <StatTile
                label={t('list.stat.latestNotice')}
                value={stats.data ? formatDate(stats.data.latest_notice_date) : '—'}
              />
              <StatTile
                label={t('list.stat.archiveBackTo')}
                value={stats.data ? formatDate(stats.data.earliest_notice_date) : '—'}
              />
            </>
          )}
        </div>
      </section>

      {stats.data?.archive && !stats.data.archive.complete && stats.data.archive.enabled && (
        <ArchiveProgressBar archive={stats.data.archive} />
      )}

      <FilterPanel
        values={filters}
        facets={facets.data}
        facetsLoading={facets.loading}
        resultCount={tenders.data?.count ?? null}
        onChange={updateFilters}
        onReset={resetFilters}
      />

      {tenders.loading && <ListSkeleton />}

      {!tenders.loading && tenders.error != null && (
        <ErrorState error={tenders.error} onRetry={tenders.reload} />
      )}

      {!tenders.loading && tenders.error == null && tenders.data && (
        <>
          {tenders.data.results.length === 0 ? (
            <EmptyState
              title={t('list.empty.title')}
              description={t(focusOn ? 'list.empty.focus' : 'list.empty.archive')}
              action={
                <button type="button" className="btn btn-primary" onClick={resetFilters}>
                  {t('filter.clear')}
                </button>
              }
            />
          ) : (
            <>
              <div className="card-grid">
                {tenders.data.results.map((notice) => (
                  <TenderCard key={notice.id} notice={notice} />
                ))}
              </div>
              <Pagination
                page={tenders.data.page}
                totalPages={tenders.data.total_pages}
                onChange={goToPage}
              />
            </>
          )}
        </>
      )}
    </>
  );
}

/** Shown only while the historical archive is still being walked. */
function ArchiveProgressBar({ archive }: { archive: ArchiveProgress }) {
  const { t } = useI18n();

  return (
    <div className="archive-progress" role="status">
      <div className="archive-progress-text">
        <span>
          {t('archive.importing', {
            percent: archive.percent,
            done: archive.partitions_completed,
            total: archive.partitions_total,
            // The plural agrees with the partition total, not the percentage.
            count: archive.partitions_total,
          })}
        </span>
        <span className="muted">
          {archive.upstream_total
            ? t('archive.storedOf', {
                stored: archive.notices_stored,
                total: archive.upstream_total,
                count: archive.notices_stored,
              })
            : t('archive.stored', {
                stored: archive.notices_stored,
                count: archive.notices_stored,
              })}
        </span>
      </div>
      <div className="archive-progress-track">
        <div
          className="archive-progress-fill"
          style={{ width: `${Math.max(1, Math.min(100, archive.percent))}%` }}
        />
      </div>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat-tile">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
