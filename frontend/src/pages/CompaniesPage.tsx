import { useCallback, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { fetchCompanies, fetchFacets } from '../api/client';
import type { CompanyRow, Facets, Paginated } from '../api/types';
import Pagination from '../components/Pagination';
import { EmptyState, ErrorState, ListSkeleton } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';
import { categoryLabel } from '../lib/categories';

/**
 * Companies that have won contracts.
 *
 * Deliberately a roster, not a leaderboard. Most winners in the archive appear
 * exactly once, so ordering by wins is a useful default but the numbers are
 * shown rather than turned into a score — a firm with four wins is not
 * measurably "stronger" than one with three at this sample size.
 */
export default function CompaniesPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { t, tv, formatDate, formatNumber, formatMoney } = useI18n();

  const filters = useMemo(
    () => ({
      search: searchParams.get('search') ?? '',
      country: searchParams.get('country') ?? '',
      category: searchParams.get('category') ?? '',
      ordering: searchParams.get('ordering') ?? '-wins',
    }),
    [searchParams],
  );
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1);

  const companies = useAsyncData<Paginated<CompanyRow>>(
    (signal) => fetchCompanies({ ...filters, page }, signal),
    [filters.search, filters.country, filters.category, filters.ordering, page],
  );
  const facets = useAsyncData<Facets>((signal) => fetchFacets(signal), []);

  const update = useCallback(
    (patch: Record<string, string>) => {
      const next = new URLSearchParams(searchParams);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      next.delete('page');
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

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
          <h1>{t('companies.title')}</h1>
          <p className="lead">{t('companies.lead')}</p>
        </div>
      </section>

      <section className="filter-panel" aria-label={t('companies.filterAria')}>
        <div className="filter-row">
          <div className="field field-grow">
            <label htmlFor="company-search">{t('filter.search')}</label>
            <input
              id="company-search"
              type="search"
              placeholder={t('companies.searchPlaceholder')}
              defaultValue={filters.search}
              onChange={(event) => update({ search: event.target.value })}
              autoComplete="off"
            />
          </div>

          <div className="field">
            <label htmlFor="company-category">{t('filter.category')}</label>
            <select
              id="company-category"
              value={filters.category}
              onChange={(event) => update({ category: event.target.value })}
            >
              <option value="">{t('filter.allCategories')}</option>
              {facets.data?.categories
                .filter((item) => item.value !== 'unknown')
                .map((item) => (
                  <option key={item.value} value={item.value}>
                    {categoryLabel(item.value, t)}
                  </option>
                ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="company-order">{t('companies.sortBy')}</label>
            <select
              id="company-order"
              value={filters.ordering}
              onChange={(event) => update({ ordering: event.target.value })}
            >
              <option value="-wins">{t('companies.sortWins')}</option>
              <option value="-latest">{t('companies.sortLatest')}</option>
              <option value="-value">{t('companies.sortValue')}</option>
              <option value="name">{t('companies.sortName')}</option>
            </select>
          </div>
        </div>

        {companies.data && (
          <div className="filter-row filter-row-secondary">
            <span className="muted">
              {t('companies.count', { count: companies.data.count })}
            </span>
          </div>
        )}
      </section>

      {companies.loading && <ListSkeleton count={4} />}

      {!companies.loading && companies.error != null && (
        <ErrorState error={companies.error} onRetry={companies.reload} />
      )}

      {!companies.loading && companies.error == null && companies.data && (
        <>
          {companies.data.results.length === 0 ? (
            <EmptyState
              title={t('companies.emptyTitle')}
              description={t('companies.emptyBody')}
            />
          ) : (
            <>
              <div className="table-wrap">
                <table className="data-table company-table">
                  <thead>
                    <tr>
                      <th scope="col">{t('companies.colName')}</th>
                      <th scope="col">{t('companies.colCountry')}</th>
                      <th scope="col" className="num">
                        {t('companies.colWins')}
                      </th>
                      <th scope="col" className="num">
                        {t('companies.colValue')}
                      </th>
                      <th scope="col">{t('companies.colLatest')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {companies.data.results.map((company) => (
                      <tr key={company.name}>
                        <td>
                          <Link
                            to={`/companies/${encodeURIComponent(company.name)}`}
                            className="company-name"
                          >
                            {company.name}
                          </Link>
                        </td>
                        <td>{tv('country', company.country) || '—'}</td>
                        <td className="num">{formatNumber(company.wins)}</td>
                        <td className="num">
                          {company.total_usd ? (
                            <>
                              {formatMoney(Number(company.total_usd), 'USD')}
                              {/* The total covers only the USD-priced awards;
                                  saying which keeps it from reading as the
                                  company's full revenue from the Bank. */}
                              {company.usd_awards < company.wins && (
                                <small className="muted">
                                  {' '}
                                  {t('companies.ofWins', {
                                    count: company.usd_awards,
                                    total: company.wins,
                                  })}
                                </small>
                              )}
                            </>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td>{formatDate(company.latest_award)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={companies.data.page}
                totalPages={companies.data.total_pages}
                onChange={goToPage}
              />
            </>
          )}
        </>
      )}
    </>
  );
}
