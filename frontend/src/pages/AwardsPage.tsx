import { useCallback, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { fetchAwards, fetchFacets } from '../api/client';
import type { AwardParticipant, AwardRow, Facets, Paginated } from '../api/types';
import Pagination from '../components/Pagination';
import { EmptyState, ErrorState, ListSkeleton } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';
import { categoryLabel, subcategoryLabel } from '../lib/categories';

/**
 * Contracts that have been decided.
 *
 * The companion to `/companies`, and the difference matters: that page
 * aggregates by supplier and answers "who wins", this one keeps the contract
 * whole and answers "what was decided, and who was in the room". A vendor
 * researching a market needs the second — the losing bidders are the
 * competitive picture, and they exist only inside a contract, never as a row
 * in a supplier roster.
 *
 * Every company is shown with the role the notice gave it. They are not merged
 * into one list of "participants": upstream publishes awarded, evaluated and
 * rejected separately, and flattening them would assert something the source
 * does not.
 */
export default function AwardsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { t } = useI18n();

  const filters = useMemo(
    () => ({
      search: searchParams.get('search') ?? '',
      country: searchParams.get('country') ?? '',
      category: searchParams.get('category') ?? '',
      subcategory: searchParams.get('subcategory') ?? '',
      role: searchParams.get('role') ?? '',
    }),
    [searchParams],
  );
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1);

  const awards = useAsyncData<Paginated<AwardRow>>(
    (signal) => fetchAwards({ ...filters, page }, signal),
    [filters.search, filters.country, filters.category, filters.subcategory, filters.role, page],
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
          <h1>{t('awards.title')}</h1>
          <p className="lead">{t('awards.lead')}</p>
        </div>
      </section>

      <section className="filter-panel" aria-label={t('awards.filterAria')}>
        <div className="filter-row">
          <div className="field field-grow">
            <label htmlFor="award-search">{t('filter.search')}</label>
            <input
              id="award-search"
              type="search"
              placeholder={t('awards.searchPlaceholder')}
              defaultValue={filters.search}
              onChange={(event) => update({ search: event.target.value })}
              autoComplete="off"
            />
          </div>

          <div className="field">
            <label htmlFor="award-category">{t('filter.category')}</label>
            <select
              id="award-category"
              value={filters.category}
              onChange={(event) =>
                // Leaving Consulting clears the sub-direction, as on the
                // tender list: a filter in the URL that is not being applied
                // is a filter the user cannot see is off.
                update({ category: event.target.value, subcategory: '' })
              }
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

          {filters.category === 'consulting' && (
            <div className="field">
              <label htmlFor="award-subcategory">{t('filter.subcategory')}</label>
              <select
                id="award-subcategory"
                value={filters.subcategory}
                onChange={(event) => update({ subcategory: event.target.value })}
              >
                <option value="">{t('filter.allSubcategories')}</option>
                {facets.data?.subcategories?.map((item) => (
                  <option key={item.value} value={item.value}>
                    {subcategoryLabel(item.value, t)}
                  </option>
                ))}
              </select>
            </div>
          )}

          <div className="field">
            <label htmlFor="award-country">{t('filter.country')}</label>
            <select
              id="award-country"
              value={filters.country}
              onChange={(event) => update({ country: event.target.value })}
            >
              <option value="">{t('filter.allCountries')}</option>
              {facets.data?.country_groups?.[0]?.countries.map((country) => (
                <option key={country.name} value={country.name}>
                  {country.name}
                </option>
              ))}
            </select>
          </div>

          {/* Not "which companies to show" but "which contracts": a contract
              only enters the evaluated view if the notice actually named the
              firms it weighed, which most do not. */}
          <div className="field">
            <label htmlFor="award-role">{t('awards.role')}</label>
            <select
              id="award-role"
              value={filters.role}
              onChange={(event) => update({ role: event.target.value })}
            >
              <option value="">{t('awards.roleAll')}</option>
              <option value="evaluated">{t('awards.roleEvaluated')}</option>
              <option value="rejected">{t('awards.roleRejected')}</option>
            </select>
          </div>
        </div>

        {awards.data && (
          <div className="filter-row filter-row-secondary">
            <span className="muted">{t('awards.count', { count: awards.data.count })}</span>
          </div>
        )}
      </section>

      {awards.loading && <ListSkeleton count={4} />}

      {!awards.loading && awards.error != null && (
        <ErrorState error={awards.error} onRetry={awards.reload} />
      )}

      {!awards.loading && awards.error == null && awards.data && (
        <>
          {awards.data.results.length === 0 ? (
            <EmptyState title={t('awards.emptyTitle')} description={t('awards.emptyBody')} />
          ) : (
            <>
              <ul className="award-feed">
                {awards.data.results.map((award) => (
                  <AwardCard key={award.notice_id} award={award} />
                ))}
              </ul>
              <Pagination
                page={awards.data.page}
                totalPages={awards.data.total_pages}
                onChange={goToPage}
              />
            </>
          )}
        </>
      )}
    </>
  );
}

function AwardCard({ award }: { award: AwardRow }) {
  const { t, tv, formatDate, formatMoney } = useI18n();

  const winners = award.participants.filter((p) => p.role === 'awardee');
  const evaluated = award.participants.filter((p) => p.role === 'evaluated');
  const rejected = award.participants.filter((p) => p.role === 'rejected');

  return (
    <li className="card award-card">
      <div className="award-head">
        <div>
          <p className="award-title">{award.title}</p>
          {award.project_name && <p className="muted small">{award.project_name}</p>}
        </div>
        <div className="award-figures">
          {award.contract_price && award.currency ? (
            <strong>{formatMoney(Number(award.contract_price), award.currency)}</strong>
          ) : null}
          <span className="muted small">{formatDate(award.award_date)}</span>
        </div>
      </div>

      <div className="award-tags">
        <span className="tag tag-quiet">{tv('country', award.country)}</span>
        {award.category && award.category !== 'unknown' && (
          <span className={`tag tag-category cat-${award.category}`}>
            {categoryLabel(award.category, t)}
          </span>
        )}
        {award.subcategory && (
          <span className="tag tag-subcategory">{subcategoryLabel(award.subcategory, t)}</span>
        )}
        {award.contract_duration && (
          <span className="tag tag-quiet">{award.contract_duration}</span>
        )}
      </div>

      <RoleGroup label={t('awards.awardee')} people={winners} tone="win" />
      <RoleGroup label={t('awards.evaluated')} people={evaluated} tone="neutral" />
      <RoleGroup label={t('awards.rejected')} people={rejected} tone="out" />

      <div className="award-links">
        <Link to={`/tenders/${encodeURIComponent(award.notice_id)}`}>
          {t('awards.openNotice')}
        </Link>
        <a href={award.source_url} target="_blank" rel="noopener noreferrer">
          {t('awards.openUpstream')}
        </a>
      </div>
    </li>
  );
}

/** One role's companies. Renders nothing when the notice named none in it. */
function RoleGroup({
  label,
  people,
  tone,
}: {
  label: string;
  people: AwardParticipant[];
  tone: 'win' | 'neutral' | 'out';
}) {
  const { tv } = useI18n();
  if (people.length === 0) return null;

  return (
    <div className={`award-role award-role-${tone}`}>
      <span className="award-role-label">{label}</span>
      <ul className="award-role-list">
        {people.map((person, index) => (
          <li key={`${person.name}-${index}`}>
            {person.website ? (
              <a href={person.website} target="_blank" rel="noopener noreferrer">
                {person.name}
              </a>
            ) : (
              person.name
            )}
            {person.country && (
              <span className="muted small"> · {tv('country', person.country)}</span>
            )}
            {/* Why it was thrown out, in the notice's own words. */}
            {person.reason && <span className="award-reason"> · {person.reason}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
