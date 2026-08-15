import { useCallback, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';

import { fetchExpertTypes, fetchExperts } from '../api/client';
import type { Expert, ExpertFamily, Paginated } from '../api/types';
import Pagination from '../components/Pagination';
import { EmptyState, ErrorState, ListSkeleton } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';

/**
 * The expert directory, browsable on its own.
 *
 * It exists because of where the compliance verdict stops. A tender names a
 * Resettlement Specialist, the vendor has none, and the honest answer — "you
 * cannot bid" — is a dead end unless the next screen is somewhere to look. So
 * this page is reachable from the verdict *and* from the header: vendors also
 * arrive already knowing which seat is empty.
 *
 * Every filter lives in the URL rather than in component state, so a shortlist
 * is a link someone can send to a colleague. That matters more here than on
 * the tender list: finding an expert is work several people do together.
 *
 * Nothing on this page is a claim about any tender. The roles are our
 * taxonomy, the people are our directory, and neither was read out of a notice
 * — which is why the page carries no evidence quotes and no verdicts, and the
 * per-notice panel that *does* carry them is a different component.
 */
export default function ExpertsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { t } = useI18n();

  const filters = useMemo(
    () => ({
      search: searchParams.get('search') ?? '',
      role: searchParams.get('role') ?? '',
      family: searchParams.get('family') ?? '',
      ordering: searchParams.get('ordering') ?? 'full_name',
    }),
    [searchParams],
  );
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1);

  const taxonomy = useAsyncData<ExpertFamily[]>((signal) => fetchExpertTypes(signal), []);
  const experts = useAsyncData<Paginated<Expert>>(
    (signal) =>
      fetchExperts(
        {
          search: filters.search,
          // A role is the narrower of the two, so it wins: sending both would
          // ask for people who hold this role *and* something in that field,
          // which is a question the filter row does not look like it is asking.
          role: filters.role ? [filters.role] : undefined,
          family: filters.role ? undefined : filters.family,
          ordering: filters.ordering,
          page,
        },
        signal,
      ),
    [filters.search, filters.role, filters.family, filters.ordering, page],
  );

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

  const families = taxonomy.data ?? [];
  // Roles are grouped under their family in the picker. A flat list of 36 is
  // unreadable, and the family a role sits in is most of what identifies it —
  // "Economist" means something different under Finance than it would anywhere
  // else.
  const hasFilters = Boolean(filters.search || filters.role || filters.family);

  return (
    <>
      <section className="page-head">
        <div>
          <h1>{t('experts.title')}</h1>
          <p className="lead">{t('experts.lead')}</p>
        </div>
      </section>

      <section className="filter-panel" aria-label={t('experts.filterAria')}>
        <div className="filter-row">
          <div className="field field-grow">
            <label htmlFor="expert-search">{t('filter.search')}</label>
            <input
              id="expert-search"
              type="search"
              placeholder={t('experts.searchPlaceholder')}
              defaultValue={filters.search}
              onChange={(event) => update({ search: event.target.value })}
              autoComplete="off"
            />
          </div>

          <div className="field">
            <label htmlFor="expert-family">{t('experts.family')}</label>
            <select
              id="expert-family"
              value={filters.family}
              onChange={(event) => update({ family: event.target.value, role: '' })}
            >
              <option value="">{t('experts.allFamilies')}</option>
              {families.map((family) => (
                <option key={family.slug} value={family.slug}>
                  {family.name}
                </option>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="expert-role">{t('experts.role')}</label>
            <select
              id="expert-role"
              value={filters.role}
              onChange={(event) => update({ role: event.target.value })}
            >
              <option value="">{t('experts.allRoles')}</option>
              {families.map((family) => (
                <optgroup key={family.slug} label={family.name}>
                  {family.roles.map((role) => (
                    <option key={role.slug} value={role.slug}>
                      {role.name}
                      {/* The count is what turns the picker from a list of
                          options into a map of where the directory is thin. */}
                      {typeof role.expert_count === 'number'
                        ? ` (${role.expert_count})`
                        : ''}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <div className="field">
            <label htmlFor="expert-order">{t('experts.sortBy')}</label>
            <select
              id="expert-order"
              value={filters.ordering}
              onChange={(event) => update({ ordering: event.target.value })}
            >
              <option value="full_name">{t('experts.sortName')}</option>
              <option value="-full_name">{t('experts.sortNameDesc')}</option>
              <option value="-updated_at">{t('experts.sortUpdated')}</option>
            </select>
          </div>
        </div>

        {experts.data && (
          <div className="filter-row filter-row-secondary">
            <span className="muted">{t('experts.count', { count: experts.data.count })}</span>
            {hasFilters && (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => setSearchParams(new URLSearchParams(), { replace: true })}
              >
                {t('experts.clear')}
              </button>
            )}
          </div>
        )}
      </section>

      {experts.loading && <ListSkeleton count={4} />}

      {!experts.loading && experts.error != null && (
        <ErrorState error={experts.error} onRetry={experts.reload} />
      )}

      {!experts.loading && experts.error == null && experts.data && (
        <>
          {experts.data.results.length === 0 ? (
            <EmptyState title={t('experts.emptyTitle')} description={t('experts.emptyBody')} />
          ) : (
            <>
              <div className="table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">{t('experts.colName')}</th>
                      <th scope="col">{t('experts.colRoles')}</th>
                      <th scope="col">{t('experts.colProfile')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {experts.data.results.map((expert) => (
                      <tr key={expert.id}>
                        <td>
                          <strong>{expert.full_name}</strong>
                        </td>
                        <td>
                          <span className="tag-row">
                            {expert.roles.map((role) => (
                              <button
                                key={role.slug}
                                type="button"
                                className="tag tag-button"
                                onClick={() => update({ role: role.slug, family: '' })}
                                title={role.family_name}
                              >
                                {role.name}
                              </button>
                            ))}
                          </span>
                        </td>
                        <td>
                          {expert.linkedin_url ? (
                            <a
                              href={expert.linkedin_url}
                              target="_blank"
                              // `noopener` because the opened page must not be
                              // able to reach back through `window.opener`.
                              rel="noopener noreferrer"
                            >
                              {t('experts.profileLink')}
                            </a>
                          ) : (
                            <span className="muted">{t('experts.noProfile')}</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={experts.data.page}
                totalPages={experts.data.total_pages}
                onChange={goToPage}
              />
            </>
          )}
        </>
      )}
    </>
  );
}
