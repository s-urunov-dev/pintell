import { useEffect, useState } from 'react';

import type { Facets } from '../api/types';
import { useI18n } from '../i18n';
import { audienceLabel, categoryLabel, subcategoryLabel } from '../lib/categories';

export interface FilterValues {
  country: string;
  procurement_method: string;
  notice_type: string;
  category: string;
  subcategory: string;
  consulting_audience: string;
  focus: string;
  is_open: string;
  search: string;
}

interface FilterPanelProps {
  values: FilterValues;
  facets: Facets | null;
  facetsLoading: boolean;
  resultCount: number | null;
  onChange: (patch: Partial<FilterValues>) => void;
  onReset: () => void;
}

export default function FilterPanel({
  values,
  facets,
  facetsLoading,
  resultCount,
  onChange,
  onReset,
}: FilterPanelProps) {
  const { t, tv, formatNumber } = useI18n();
  // Debounce the search box so typing does not fire a request per keystroke.
  const [searchDraft, setSearchDraft] = useState(values.search);

  useEffect(() => setSearchDraft(values.search), [values.search]);

  useEffect(() => {
    if (searchDraft === values.search) return;
    const timer = setTimeout(() => onChange({ search: searchDraft }), 350);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft]);

  const focusOn = values.focus !== 'false';
  const group = facets?.country_groups?.[0];

  const hasActiveFilters =
    Boolean(
      values.country ||
        values.procurement_method ||
        values.notice_type ||
        values.category ||
        values.subcategory ||
        values.consulting_audience ||
        values.search,
    ) || values.is_open !== '';

  return (
    <section className="filter-panel" aria-label={t('filter.aria')}>
      {/* The focus switch is gone. It offered to turn off the one thing the
          product is — open opportunities in the focus region — and the mirror
          holds nothing outside that region anyway, so switching it off bought
          closed notices and award notices, not a wider country list.

          The URL parameter still works and every `focusOn` branch below still
          reads it: `?focus=false` remains a way to look at the whole archive
          for whoever is debugging one. It is simply not a control offered to
          a vendor. */}
      <div className="focus-row">
        {focusOn && group && (
          <ul className="flag-strip" aria-label={t('filter.countriesAria')}>
            {group.countries.map((country) => {
              const active = values.country === country.name;
              // Zero here means zero under the current filters, so the click
              // would only ever land on an empty list.
              const empty = country.count === 0 && !active;
              return (
                <li key={country.name}>
                  <button
                    type="button"
                    className={`flag-chip ${active ? 'active' : ''}`}
                    aria-pressed={active}
                    disabled={empty}
                    // Clicking the active country clears it, so the strip is
                    // its own "off" switch and needs no separate reset.
                    onClick={() => onChange({ country: active ? '' : country.name })}
                    title={
                      country.note
                        ? tv('region', country.note)
                        : tv('country', country.name)
                    }
                  >
                    <span aria-hidden="true">{country.flag ?? '•'}</span>
                    <span className="flag-name">{tv('country', country.name)}</span>
                    <span className="flag-count">{formatNumber(country.count)}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="filter-row">
        <div className="field field-grow">
          <label htmlFor="filter-search">{t('filter.search')}</label>
          <input
            id="filter-search"
            type="search"
            placeholder={t('filter.searchPlaceholder')}
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            autoComplete="off"
          />
        </div>

        <div className="field">
          <label htmlFor="filter-category">{t('filter.category')}</label>
          <select
            id="filter-category"
            value={values.category}
            disabled={facetsLoading}
            onChange={(event) =>
              // Leaving Consulting clears both consulting-only filters, so the
              // URL can never carry one that is not being applied.
              onChange({
                category: event.target.value,
                subcategory: '',
                consulting_audience: '',
              })
            }
          >
            <option value="">{t('filter.allCategories')}</option>
            {facets?.categories
              .filter((item) => item.value !== 'unknown')
              .map((item) => (
                <option key={item.value} value={item.value}>
                  {categoryLabel(item.value, t)} ({formatNumber(item.count)})
                </option>
              ))}
          </select>
        </div>

        {/* Only Consulting is sub-divided, so the control appears only when
            that direction is selected — an empty dropdown on every other
            direction would be a dead end. */}
        {values.category === 'consulting' && (
          <div className="field">
            <label htmlFor="filter-subcategory">{t('filter.subcategory')}</label>
            <select
              id="filter-subcategory"
              value={values.subcategory}
              disabled={facetsLoading}
              onChange={(event) => onChange({ subcategory: event.target.value })}
            >
              <option value="">{t('filter.allSubcategories')}</option>
              {facets?.subcategories?.map((item) => (
                <option key={item.value} value={item.value}>
                  {subcategoryLabel(item.value, t)} ({formatNumber(item.count)})
                </option>
              ))}
            </select>
          </div>
        )}

        {/* The second consulting axis, and the one that decides whether a
            notice is answerable at all: a firm cannot bid for an individual
            selection, and a freelancer cannot field a corporate track record.
            Kept as its own control rather than folded into the sub-direction
            so "individual IT advisory" stays askable. */}
        {values.category === 'consulting' && (
          <div className="field">
            <label htmlFor="filter-audience">{t('filter.audience')}</label>
            <select
              id="filter-audience"
              value={values.consulting_audience}
              disabled={facetsLoading}
              onChange={(event) => onChange({ consulting_audience: event.target.value })}
            >
              <option value="">{t('filter.allAudiences')}</option>
              {facets?.consulting_audiences?.map((item) => (
                <option key={item.value} value={item.value}>
                  {audienceLabel(item.value, t)} ({formatNumber(item.count)})
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="field">
          <label htmlFor="filter-country">{t('filter.country')}</label>
          <select
            id="filter-country"
            value={values.country}
            disabled={facetsLoading}
            onChange={(event) => onChange({ country: event.target.value })}
          >
            <option value="">{t('filter.allCountries')}</option>
            {/* The option *value* stays the upstream English name — that is
                what the API filters on — while the label is translated. */}
            {(focusOn && group
              ? group.countries.map((c) => ({ value: c.name, count: c.count }))
              : (facets?.countries ?? []).map((c) => ({ value: c.value, count: c.count }))
            ).map((item) => (
              <option key={item.value} value={item.value}>
                {tv('country', item.value)} ({formatNumber(item.count)})
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="filter-method">{t('filter.method')}</label>
          <select
            id="filter-method"
            value={values.procurement_method}
            disabled={facetsLoading}
            onChange={(event) => onChange({ procurement_method: event.target.value })}
          >
            <option value="">{t('filter.allMethods')}</option>
            {facets?.procurement_methods.map((item) => (
              <option key={item.value} value={item.value}>
                {tv('procurementMethod', item.label ?? item.value)} (
                {formatNumber(item.count)})
              </option>
            ))}
          </select>
        </div>

        {!focusOn && (
          <div className="field">
            <label htmlFor="filter-type">{t('filter.noticeType')}</label>
            <select
              id="filter-type"
              value={values.notice_type}
              disabled={facetsLoading}
              onChange={(event) => onChange({ notice_type: event.target.value })}
            >
              <option value="">{t('filter.allTypes')}</option>
              {facets?.notice_types.map((item) => (
                <option key={item.value} value={item.value}>
                  {tv('noticeType', item.value)} ({formatNumber(item.count)})
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="filter-row filter-row-secondary">
        {!focusOn && (
          <div className="segmented" role="group" aria-label={t('filter.deadlineStatusAria')}>
            {[
              { label: t('filter.statusAll'), value: '' },
              { label: t('filter.statusOpen'), value: 'true' },
              { label: t('filter.statusClosed'), value: 'false' },
            ].map((option) => (
              <button
                key={option.value || 'all'}
                type="button"
                className={values.is_open === option.value ? 'active' : ''}
                aria-pressed={values.is_open === option.value}
                onClick={() => onChange({ is_open: option.value })}
              >
                {option.label}
              </button>
            ))}
          </div>
        )}

        <div className="filter-summary">
          {resultCount !== null && (
            <span className="muted">{t('filter.resultCount', { count: resultCount })}</span>
          )}
          {hasActiveFilters && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={onReset}>
              {t('filter.clear')}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
