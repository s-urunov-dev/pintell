import type { ReactNode } from 'react';

import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

export interface Column<T> {
  key: string;
  header: string;
  /** Cell renderer; defaults to the raw value at `key`. */
  render?: (row: T) => ReactNode;
  align?: 'left' | 'right';
  width?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  loading?: boolean;
  /** The rejection itself — translated here, at render time. */
  error?: unknown;
  /** Already-translated "no rows" text; falls back to a generic one. */
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
  activeRowKey?: string | number | null;
}

export default function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error = null,
  emptyMessage,
  onRowClick,
  activeRowKey = null,
}: DataTableProps<T>) {
  const { t } = useI18n();

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                style={{ width: column.width, textAlign: column.align ?? 'left' }}
                scope="col"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading &&
            Array.from({ length: 6 }, (_, index) => (
              <tr key={`skeleton-${index}`} aria-hidden="true">
                {columns.map((column) => (
                  <td key={column.key}>
                    <span className="skeleton" style={{ width: '70%' }} />
                  </td>
                ))}
              </tr>
            ))}

          {!loading && error != null && (
            <tr>
              <td colSpan={columns.length} className="table-message table-error">
                {errorMessage(error, t)}
              </td>
            </tr>
          )}

          {!loading && error == null && rows.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="table-message muted">
                {emptyMessage ?? t('table.empty')}
              </td>
            </tr>
          )}

          {!loading &&
            error == null &&
            rows.map((row) => {
              const key = rowKey(row);
              return (
                <tr
                  key={key}
                  className={[
                    onRowClick ? 'clickable' : '',
                    activeRowKey === key ? 'active' : '',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={
                    onRowClick
                      ? (event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            onRowClick(row);
                          }
                        }
                      : undefined
                  }
                >
                  {columns.map((column) => (
                    <td key={column.key} style={{ textAlign: column.align ?? 'left' }}>
                      {column.render
                        ? column.render(row)
                        : String((row as Record<string, unknown>)[column.key] ?? '—')}
                    </td>
                  ))}
                </tr>
              );
            })}
        </tbody>
      </table>
    </div>
  );
}
