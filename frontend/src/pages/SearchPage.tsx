import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { searchArchive } from '../api/client';
import type { SearchResponse, SearchResult } from '../api/types';
import { useCitation } from '../components/CitationDock';
import { EmptyState, ErrorState, ListSkeleton } from '../components/StateViews';
import { useI18n } from '../i18n';
import { CATEGORY_KEYS, categoryLabel } from '../lib/categories';

/**
 * Asking the archive a question in words, and being able to check the answer.
 *
 * The tender list answers "which notices match these filters". This answers a
 * different question — "where does this corpus talk about advance payment
 * guarantees" — over the notice bodies *and* the mirrored bidding documents,
 * which the list cannot search at all because their text is not a column on a
 * notice.
 *
 * **Every result opens its own source.** That is the whole design constraint,
 * and it is why a result row is a passage with a citation badge rather than a
 * summary: a retrieved paragraph that cannot be traced back to a page is
 * indistinguishable from one a model wrote, and this product does not ship
 * claims without a source. The badge carries the position; pressing it renders
 * the borrower's page with the passage boxed.
 *
 * **The page says how it answered.** When the semantic index is unavailable or
 * empty the server falls back to Postgres full-text, and the banner says so.
 * Hiding that would be the tempting choice and the wrong one: keyword results
 * and semantic ones are not the same quality of answer, and a vendor reading
 * five weak matches deserves to know the index was not consulted.
 *
 * The query lives in the URL, so a search is a link a colleague can open.
 */
export default function SearchPage() {
  const { t } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();

  const query = searchParams.get('q') ?? '';
  const category = searchParams.get('category') ?? '';

  // The box is local state and the URL is the committed search: typing must
  // not fire a metered embedding call per keystroke, and it must not push a
  // history entry per keystroke either.
  const [draft, setDraft] = useState(query);
  useEffect(() => setDraft(query), [query]);

  const [response, setResponse] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const { open: openCitation } = useCitation();

  useEffect(() => {
    if (!query.trim()) {
      setResponse(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    searchArchive(query, { category: category || undefined }, controller.signal)
      .then(setResponse)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return;
        setError(cause);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, [query, category]);

  const commit = useCallback(
    (next: string, nextCategory: string) => {
      const params = new URLSearchParams();
      if (next.trim()) params.set('q', next.trim());
      if (nextCategory) params.set('category', nextCategory);
      setSearchParams(params);
    },
    [setSearchParams],
  );

  const banner = useMemo(() => bannerKey(response), [response]);

  return (
    <div className="page search-page">
      <header className="page-head">
        <h1>{t('search.heading')}</h1>
        <p className="muted">{t('search.subtitle')}</p>
      </header>

      <form
        className="search-form"
        onSubmit={(event) => {
          event.preventDefault();
          commit(draft, category);
        }}
      >
        <input
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={t('search.placeholder')}
          aria-label={t('search.heading')}
          maxLength={2000}
        />
        <select
          value={category}
          onChange={(event) => commit(draft, event.target.value)}
          aria-label={t('search.filter.category')}
        >
          <option value="">{t('search.filter.allCategories')}</option>
          {CATEGORY_KEYS.map((slug) => (
            <option key={slug} value={slug}>
              {categoryLabel(slug, t)}
            </option>
          ))}
        </select>
        <button type="submit">{t('search.submit')}</button>
      </form>

      {banner && <p className="search-banner">{t(banner)}</p>}

      {loading && <ListSkeleton />}
      {/* Ternary rather than `&&`: the state is `unknown` (the rejection
          itself, translated at render time), and `unknown &&` is not a
          ReactNode. */}
      {error ? <ErrorState error={error} onRetry={() => commit(draft, category)} /> : null}

      {!loading && !error && response && response.results.length === 0 && (
        <EmptyState title={t('search.noResults')} description={t('search.noResultsHint')} />
      )}

      {!loading && !error && response && response.results.length > 0 && (
        <ol className="search-results">
          {response.results.map((result) => (
            <li key={`${result.payload.source_key}:${result.payload.position_id}`}>
              <ResultRow result={result} onOpen={() => openCitation(result)} />
            </li>
          ))}
        </ol>
      )}

    </div>
  );
}

/**
 * One retrieved passage, with the badge that opens its source.
 *
 * The score is shown, and it is shown *with* the retrieval path beside it,
 * because the two paths produce numbers on different scales. Printing a bare
 * "0.62" under results that came from keyword ranking would invite exactly the
 * comparison the API refuses to make.
 */
function ResultRow({ result, onOpen }: { result: SearchResult; onOpen: () => void }) {
  const { t } = useI18n();
  const { payload } = result;
  const isPdf = payload.source_type === 'pdf';

  return (
    <article className="search-result">
      <p className="search-passage">{result.content}</p>

      <footer className="search-result-meta">
        <button type="button" className="citation-badge" onClick={onOpen}>
          {isPdf
            ? t('search.badge.page', { page: payload.page ?? 1 })
            : t('search.badge.text')}
        </button>

        <a href={`/tenders/${encodeURIComponent(result.notice_id)}`} className="search-tender">
          {payload.title || result.notice_id}
        </a>

        <span className="muted small">
          {t(
            result.retrieval === 'vector'
              ? 'search.retrieval.vector'
              : 'search.retrieval.fts',
            { score: result.score.toFixed(2) },
          )}
        </span>
      </footer>
    </article>
  );
}

/**
 * Which banner the response earns, if any.
 *
 * Keyed off `degraded_reason` rather than off an empty result list, because
 * "the index has nothing for this question" and "the index was never built"
 * look identical on screen and mean opposite things to whoever is reading.
 */
function bannerKey(response: SearchResponse | null) {
  if (!response) return null;
  switch (response.degraded_reason) {
    case 'embeddings_unavailable':
      return 'search.degraded.embeddings' as const;
    case 'vector_store_unavailable':
      return 'search.degraded.store' as const;
    case 'no_vector_match':
      return response.results.length > 0 ? ('search.degraded.keyword' as const) : null;
    default:
      return null;
  }
}
