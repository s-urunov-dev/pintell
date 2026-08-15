import { useMemo, useState } from 'react';

import { fetchNotice, fetchNotices, resanitizeNotice } from '../api/client';
import type { AdminNotice, AdminNoticeDetail, Paginated } from '../api/types';
import DataTable, { type Column } from '../components/DataTable';
import Pagination from '../components/Pagination';
import SafeHtml from '../components/SafeHtml';
import {
  ActionButton,
  BootScreen,
  type Feedback,
  FeedbackBanner,
  Panel,
} from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { useDocumentTitle, useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

type BodyView = 'sanitized' | 'raw' | 'rendered';

export default function NoticesPage() {
  const [searchInput, setSearchInput] = useState('');
  const [countryInput, setCountryInput] = useState('');
  const search = useDebouncedValue(searchInput);
  const country = useDebouncedValue(countryInput);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [detailToken, setDetailToken] = useState(0);
  const { t, formatDate, formatNumber } = useI18n();
  useDocumentTitle('title.notices');

  const notices = useAsyncData<Paginated<AdminNotice>>(
    (signal) => fetchNotices({ search, country, page, page_size: 25 }, signal),
    [search, country, page],
  );

  const detail = useAsyncData<AdminNoticeDetail | null>(
    (signal) => (selectedId ? fetchNotice(selectedId, signal) : Promise.resolve(null)),
    [selectedId, detailToken],
  );

  const columns = useMemo<Column<AdminNotice>[]>(
    () => [
      {
        key: 'id',
        header: t('notices.col.id'),
        render: (row) => <code>{row.id}</code>,
        width: '130px',
      },
      {
        key: 'bid_description',
        header: t('notices.col.description'),
        render: (row) => (
          <span title={row.bid_description}>
            {row.bid_description ? row.bid_description.slice(0, 70) : '—'}
            {row.bid_description.length > 70 ? '…' : ''}
          </span>
        ),
      },
      { key: 'country', header: t('notices.col.country'), width: '150px' },
      { key: 'notice_type', header: t('notices.col.type'), width: '160px' },
      {
        key: 'notice_date',
        header: t('notices.col.published'),
        width: '110px',
        render: (row) => formatDate(row.notice_date),
      },
      {
        key: 'body',
        header: t('notices.col.body'),
        align: 'right',
        width: '150px',
        render: (row) => (
          <span className="muted small">
            {formatNumber(row.raw_chars)} → {formatNumber(row.sanitized_chars)}
          </span>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t],
  );

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t('notices.heading')}</h1>
          <p className="muted small">{t('notices.subtitle')}</p>
        </div>
      </header>

      <FeedbackBanner feedback={feedback} />

      <Panel
        title={t('notices.panelTitle')}
        description={
          notices.data ? t('notices.recordCount', { count: notices.data.count }) : undefined
        }
        actions={
          <div className="panel-filters">
            <input
              type="search"
              placeholder={t('notices.searchPlaceholder')}
              value={searchInput}
              onChange={(event) => {
                setSearchInput(event.target.value);
                setPage(1);
              }}
            />
            <input
              type="search"
              placeholder={t('notices.countryPlaceholder')}
              value={countryInput}
              onChange={(event) => {
                setCountryInput(event.target.value);
                setPage(1);
              }}
            />
          </div>
        }
      >
        <DataTable
          columns={columns}
          rows={notices.data?.results ?? []}
          rowKey={(row) => row.id}
          loading={notices.loading}
          error={notices.error}
          emptyMessage={t('notices.empty')}
          onRowClick={(row) => setSelectedId(row.id === selectedId ? null : row.id)}
          activeRowKey={selectedId}
        />
        {notices.data && (
          <Pagination
            page={notices.data.page}
            totalPages={notices.data.total_pages}
            onChange={setPage}
          />
        )}
      </Panel>

      {selectedId && (
        <NoticeInspector
          notice={detail.data}
          loading={detail.loading}
          error={detail.error}
          onClose={() => setSelectedId(null)}
          onResanitized={(result) => {
            setFeedback(result);
            if (result?.tone === 'good') setDetailToken((token) => token + 1);
          }}
        />
      )}
    </>
  );
}

function NoticeInspector({
  notice,
  loading,
  error,
  onClose,
  onResanitized,
}: {
  notice: AdminNoticeDetail | null;
  loading: boolean;
  error: unknown;
  onClose: () => void;
  onResanitized: (feedback: Feedback) => void;
}) {
  const { t, formatDate, formatDateTime } = useI18n();
  const [view, setView] = useState<BodyView>('sanitized');

  if (loading) {
    return (
      <Panel title={t('notices.inspectorTitle')}>
        <BootScreen />
      </Panel>
    );
  }

  if (error != null) {
    return (
      <Panel title={t('notices.inspectorTitle')}>
        <div className="banner banner-critical">{errorMessage(error, t)}</div>
      </Panel>
    );
  }

  if (!notice) return null;

  const removed = notice.raw_chars - notice.sanitized_chars;

  return (
    <Panel
      title={notice.id}
      description={notice.bid_description || notice.project_name}
      actions={
        <>
          <ActionButton
            label={t('notices.resanitize')}
            variant="ghost"
            size="sm"
            onRun={async () => {
              const result = await resanitizeNotice(notice.id);
              return result.changed
                ? t('feedback.bodyRewritten', {
                    before: result.chars_before,
                    after: result.chars_after,
                  })
                : t('feedback.alreadyClean');
            }}
            onDone={onResanitized}
          />
          <a
            className="btn btn-ghost btn-sm"
            href={notice.source_url}
            target="_blank"
            rel="noreferrer"
          >
            {t('notices.upstream')}
          </a>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            {t('action.close')}
          </button>
        </>
      }
    >
      <dl className="kv-grid">
        <div>
          <dt>{t('notices.country')}</dt>
          <dd>{notice.country || '—'}</dd>
        </div>
        <div>
          <dt>{t('notices.published')}</dt>
          <dd>{formatDate(notice.notice_date)}</dd>
        </div>
        <div>
          <dt>{t('notices.deadline')}</dt>
          <dd>{formatDate(notice.deadline_date)}</dd>
        </div>
        <div>
          <dt>{t('notices.method')}</dt>
          <dd>{notice.procurement_method_name || notice.procurement_method_code || '—'}</dd>
        </div>
        <div>
          <dt>{t('notices.project')}</dt>
          <dd>{notice.project_id || '—'}</dd>
        </div>
        <div>
          <dt>{t('notices.lastSynced')}</dt>
          <dd>{formatDateTime(notice.last_synced_at)}</dd>
        </div>
        <div>
          <dt>{t('notices.contentHash')}</dt>
          <dd><code className="small">{notice.content_hash.slice(0, 16)}…</code></dd>
        </div>
        <div>
          <dt>{t('notices.sanitiser')}</dt>
          <dd>
            {t('notices.chars', {
              before: notice.raw_chars,
              after: notice.sanitized_chars,
            })}
            {removed > 0 && (
              <span className="muted"> {t('notices.charsRemoved', { count: removed })}</span>
            )}
          </dd>
        </div>
      </dl>

      <div className="segmented body-switch">
        {(
          [
            ['sanitized', t('notices.view.sanitized')],
            ['raw', t('notices.view.raw')],
            ['rendered', t('notices.view.rendered')],
          ] as [BodyView, string][]
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={view === value ? 'active' : ''}
            onClick={() => setView(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {view === 'raw' && (
        <>
          <p className="muted small">{t('notices.rawWarning')}</p>
          <pre className="code-block">
            {notice.notice_text_raw || t('notices.emptyBody')}
          </pre>
        </>
      )}

      {view === 'sanitized' && (
        <pre className="code-block">
          {notice.notice_text_sanitized || t('notices.emptyBody')}
        </pre>
      )}

      {view === 'rendered' && (
        // Allow-list renderer: no innerHTML anywhere in this app either.
        <SafeHtml className="notice-body" html={notice.notice_text_sanitized} />
      )}
    </Panel>
  );
}
