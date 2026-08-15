import { useI18n } from '../i18n';

interface PaginationProps {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
}

/** Compact page list: 1 … 4 5 [6] 7 8 … 42 */
function pageWindow(page: number, totalPages: number): (number | 'gap')[] {
  const pages = new Set<number>([1, totalPages, page]);
  for (let offset = 1; offset <= 2; offset += 1) {
    if (page - offset > 0) pages.add(page - offset);
    if (page + offset <= totalPages) pages.add(page + offset);
  }

  const sorted = [...pages].filter((n) => n >= 1 && n <= totalPages).sort((a, b) => a - b);
  const result: (number | 'gap')[] = [];
  let previous = 0;
  for (const value of sorted) {
    if (previous && value - previous > 1) result.push('gap');
    result.push(value);
    previous = value;
  }
  return result;
}

export default function Pagination({ page, totalPages, onChange }: PaginationProps) {
  const { t, formatNumber } = useI18n();
  if (totalPages <= 1) return null;

  return (
    <nav className="pagination" aria-label={t('pagination.aria')}>
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => onChange(page - 1)}
        disabled={page <= 1}
      >
        {t('pagination.previous')}
      </button>

      <ul className="page-list">
        {pageWindow(page, totalPages).map((item, index) =>
          item === 'gap' ? (
            <li key={`gap-${index}`} className="page-gap" aria-hidden="true">
              …
            </li>
          ) : (
            <li key={item}>
              <button
                type="button"
                className={`page-btn ${item === page ? 'active' : ''}`}
                aria-current={item === page ? 'page' : undefined}
                aria-label={t('pagination.page', { page: item })}
                onClick={() => onChange(item)}
              >
                {formatNumber(item)}
              </button>
            </li>
          ),
        )}
      </ul>

      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => onChange(page + 1)}
        disabled={page >= totalPages}
      >
        {t('pagination.next')}
      </button>
    </nav>
  );
}
