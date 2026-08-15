import { useState } from 'react';

import { fetchSystemStatus, triggerBackfill, triggerSync } from '../api/client';
import type { SystemStatus } from '../api/types';
import {
  ActionButton,
  BootScreen,
  type Feedback,
  FeedbackBanner,
  Panel,
  StatCard,
} from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDocumentTitle, useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

export default function SystemPage() {
  const { data, loading, error, reload } = useAsyncData<SystemStatus>(
    (signal) => fetchSystemStatus(signal),
    [],
  );
  const [feedback, setFeedback] = useState<Feedback>(null);

  const [syncPages, setSyncPages] = useState('');
  const [syncCountry, setSyncCountry] = useState('');
  const [syncMethod, setSyncMethod] = useState('');
  const [backfillPages, setBackfillPages] = useState('');
  const [backfillPartition, setBackfillPartition] = useState('');
  const { t, formatNumber } = useI18n();
  useDocumentTitle('title.system');

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t('system.heading')}</h1>
          <p className="muted small">{t('system.subtitle')}</p>
        </div>
        <button type="button" className="btn btn-ghost" onClick={reload}>
          {t('action.refresh')}
        </button>
      </header>

      <FeedbackBanner feedback={feedback} />

      {loading && !data && <BootScreen />}
      {error != null && (
        <div className="banner banner-critical">{errorMessage(error, t)}</div>
      )}

      {data && (
        <>
          <div className="stat-row">
            <StatCard
              label={t('system.database')}
              value={t(data.database.ok ? 'system.ok' : 'system.down')}
              hint={data.database.detail}
              tone={data.database.ok ? 'good' : 'critical'}
            />
            <StatCard
              label={t('system.cache')}
              value={t(data.cache.ok ? 'system.ok' : 'system.down')}
              hint={data.cache.detail}
              tone={data.cache.ok ? 'good' : 'critical'}
            />
            <StatCard
              label={t('system.workers')}
              value={formatNumber(data.celery.workers.length)}
              hint={data.celery.detail}
              tone={data.celery.ok ? 'good' : 'warning'}
            />
          </div>

          {data.celery.workers.length > 0 && (
            <Panel title={t('system.workersTitle')}>
              <ul className="plain-list">
                {data.celery.workers.map((worker) => (
                  <li key={worker}><code>{worker}</code></li>
                ))}
              </ul>
            </Panel>
          )}

          <div className="grid-2">
            <Panel
              title={t('system.syncTitle')}
              description={t('system.syncHint')}
            >
              <div className="form-row">
                <div className="field">
                  <label htmlFor="sync-pages">{t('system.pages')}</label>
                  <input
                    id="sync-pages"
                    type="number"
                    min={1}
                    max={200}
                    placeholder={t('system.defaultPlaceholder')}
                    value={syncPages}
                    onChange={(event) => setSyncPages(event.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="sync-country">{t('system.country')}</label>
                  <input
                    id="sync-country"
                    placeholder={t('system.countryPlaceholder')}
                    value={syncCountry}
                    onChange={(event) => setSyncCountry(event.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="sync-method">{t('system.methodCode')}</label>
                  <input
                    id="sync-method"
                    placeholder={t('system.methodPlaceholder')}
                    value={syncMethod}
                    onChange={(event) => setSyncMethod(event.target.value)}
                  />
                </div>
              </div>
              <ActionButton
                label={t('system.queueSync')}
                onRun={async () => {
                  const result = await triggerSync({
                    pages: syncPages ? Number(syncPages) : undefined,
                    country: syncCountry || undefined,
                    method: syncMethod || undefined,
                  });
                  return t('feedback.syncQueued', { task: result.task_id.slice(0, 8) });
                }}
                onDone={setFeedback}
              />
            </Panel>

            <Panel
              title={t('system.backfillTitle')}
              description={t('system.backfillHint')}
            >
              <div className="form-row">
                <div className="field">
                  <label htmlFor="backfill-pages">{t('system.pages')}</label>
                  <input
                    id="backfill-pages"
                    type="number"
                    min={1}
                    max={500}
                    placeholder={t('system.defaultPlaceholder')}
                    value={backfillPages}
                    onChange={(event) => setBackfillPages(event.target.value)}
                  />
                </div>
                <div className="field field-grow">
                  <label htmlFor="backfill-partition">{t('system.partitionKey')}</label>
                  <input
                    id="backfill-partition"
                    placeholder={t('system.partitionPlaceholder')}
                    value={backfillPartition}
                    onChange={(event) => setBackfillPartition(event.target.value)}
                  />
                </div>
              </div>
              <ActionButton
                label={t('system.queueBackfill')}
                onRun={async () => {
                  const result = await triggerBackfill({
                    pages: backfillPages ? Number(backfillPages) : undefined,
                    partition_key: backfillPartition || undefined,
                  });
                  return t('feedback.backfillQueued', { partition: result.partition });
                }}
                onDone={setFeedback}
              />
            </Panel>
          </div>

          <Panel
            title={t('system.configTitle')}
            description={t('system.configHint')}
          >
            <table className="data-table config-table">
              <tbody>
                {Object.entries(data.configuration).map(([key, value]) => (
                  <tr key={key}>
                    <th scope="row">{key.replace(/_/g, ' ')}</th>
                    <td>
                      <code>{value === null ? '—' : String(value)}</code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </>
      )}
    </>
  );
}
