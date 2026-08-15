import { Link, useParams } from 'react-router-dom';

import { fetchCompany } from '../api/client';
import type { CompanyDetail } from '../api/types';
import { DetailSkeleton, ErrorState } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n } from '../i18n';
import { categoryLabel } from '../lib/categories';

/** One supplier: what it has won, where, and in which directions. */
export default function CompanyDetailPage() {
  const { name = '' } = useParams();
  const { t, tv, formatDate, formatNumber, formatMoney } = useI18n();

  const { data, loading, error, reload } = useAsyncData<CompanyDetail>(
    (signal) => fetchCompany(name, signal),
    [name],
  );

  if (loading) return <DetailSkeleton />;

  if (error != null) {
    return (
      <>
        <BackLink />
        <ErrorState error={error} onRetry={reload} />
      </>
    );
  }

  if (!data) return null;

  return (
    <article className="detail">
      <BackLink />

      <header className="detail-head">
        <h1>{data.name}</h1>
        {data.country && <p className="lead">{tv('country', data.country)}</p>}

        <dl className="fact-strip">
          <Fact label={t('companies.colWins')} value={formatNumber(data.wins)} strong />
          <Fact
            label={t('companies.colValue')}
            value={
              data.total_usd
                ? `${formatMoney(Number(data.total_usd), 'USD')}${
                    data.usd_awards < data.wins
                      ? ` ${t('companies.ofWins', {
                          count: data.usd_awards,
                          total: data.wins,
                        })}`
                      : ''
                  }`
                : null
            }
          />
          <Fact label={t('companies.firstAward')} value={formatDate(data.first_award)} />
          <Fact label={t('companies.colLatest')} value={formatDate(data.latest_award)} />
        </dl>

        {data.website && (
          <p className="company-site">
            <a href={data.website} target="_blank" rel="noopener noreferrer nofollow">
              {data.website}
            </a>
            {/* Found by search rather than published by the Bank, so it is
                labelled as such wherever it appears. */}
            {data.website_source === 'ai_web_search' && (
              <span className="muted small"> · {t('award.websiteNote')}</span>
            )}
          </p>
        )}
      </header>

      <div className="grid-2">
        {data.by_category.length > 0 && (
          <section className="card">
            <h2 className="section-title">{t('companies.byCategory')}</h2>
            <ul className="tally-list">
              {data.by_category.map((row) => (
                <li key={row.value}>
                  <span>{categoryLabel(row.value, t)}</span>
                  <strong>{formatNumber(row.count)}</strong>
                </li>
              ))}
            </ul>
          </section>
        )}

        {data.by_country.length > 0 && (
          <section className="card">
            <h2 className="section-title">{t('companies.byCountry')}</h2>
            <ul className="tally-list">
              {data.by_country.map((row) => (
                <li key={row.value}>
                  <span>{tv('country', row.value)}</span>
                  <strong>{formatNumber(row.count)}</strong>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <section className="card">
        <h2 className="section-title">
          {t('companies.awards')}{' '}
          <span className="muted">({formatNumber(data.awards.length)})</span>
        </h2>
        <ul className="award-list">
          {data.awards.map((award) => (
            <li key={award.notice_id}>
              <div className="award-list-main">
                <Link to={`/tenders/${encodeURIComponent(award.notice_id)}`}>
                  {award.notice_title || award.notice_id}
                </Link>
                <span className="muted small">
                  {[
                    tv('country', award.notice_country),
                    award.notice_category !== 'unknown'
                      ? categoryLabel(award.notice_category, t)
                      : '',
                    formatDate(award.award_date),
                  ]
                    .filter((part) => part && part !== '—')
                    .join(' · ')}
                </span>
              </div>
              {award.contract_price && (
                <span className="award-list-price">
                  {formatMoney(Number(award.contract_price), award.currency)}
                </span>
              )}
            </li>
          ))}
        </ul>
      </section>
    </article>
  );
}

function Fact({
  label,
  value,
  strong,
}: {
  label: string;
  value: string | null;
  strong?: boolean;
}) {
  if (!value) return null;
  return (
    <div className={strong ? 'fact-strong' : undefined}>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function BackLink() {
  const { t } = useI18n();
  return (
    <Link to="/companies" className="back-link">
      <span aria-hidden="true">←</span> {t('companies.back')}
    </Link>
  );
}
