import { useEffect, useRef, useState } from 'react';

import { fetchComplianceStatus, triggerExtraction } from '../api/client';
import type { ComplianceStatus, ExtractionRunRow } from '../api/types';
import {
  ActionButton,
  BootScreen,
  type Feedback,
  FeedbackBanner,
  Panel,
  ProgressBar,
  StatCard,
} from '../components/ui';
import { useAsyncData } from '../hooks/useAsyncData';
import { useDocumentTitle, useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

/**
 * What the compliance extraction is doing, live.
 *
 * Refreshed faster than the other console pages — ten seconds against thirty —
 * because this is the one screen someone watches *while* something happens:
 * a sync lands, the extraction follows it, and the pending count drains. On
 * the other pages a refresh is how you check a number; here it is the point.
 *
 * The screen is organised around one question an operator actually has —
 * "is every open tender read?" — and everything else is subordinate to it.
 */
const REFRESH_MS = 10_000;

/**
 * While a cycle is draining, the same poll is the progress bar — so it runs
 * faster. Three seconds against ten: the queue is around thirty notices and a
 * metered notice takes seconds, so at the slower rate a whole cycle can finish
 * between two frames and the screen never shows it happening.
 */
const REFRESH_RUNNING_MS = 3_000;

/**
 * How long a cycle may produce nothing before the banner stops claiming it is
 * running. A worker that died mid-batch would otherwise leave the screen
 * asserting work is in flight forever — the counts below stay correct either
 * way, so this only removes a claim the page can no longer support. Generous
 * because one L3 notice can be three long documents.
 */
const STALL_MS = 120_000;

/** What one press set going, so its progress can be measured against it. */
type Cycle = {
  /** `runs_total` the moment the button was pressed. */
  from: number;
  /** Notices this press will read — the batch bound, not the whole queue. */
  expected: number;
};

export default function CompliancePage() {
  const { data, loading, error, reload } = useAsyncData<ComplianceStatus>(
    (signal) => fetchComplianceStatus(signal),
    [],
  );
  const [feedback, setFeedback] = useState<Feedback>(null);
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const { t, formatDateTime, formatNumber } = useI18n();
  useDocumentTitle('title.compliance');

  const pending = data?.active_pending ?? 0;
  const batchSize = data?.batch_size ?? 0;
  // One press queues one batch. Promising the whole queue on the button and
  // then reading a batch of it would be the same kind of lie this page was
  // fixed to stop telling; the remainder is what the next press is for.
  const thisPress = batchSize > 0 ? Math.min(pending, batchSize) : pending;

  // Progress is counted in runs actually written since the press, not in
  // coverage: a run that failed, and a run that found nothing, are both a
  // notice this cycle has finished with, and neither moves `active_read`.
  const done = cycle ? Math.max((data?.runs_total ?? 0) - cycle.from, 0) : 0;

  // A cycle ends when its batch has landed, or when there is nothing left to
  // read at all — the second because the queued task is not the only thing that
  // drains the queue, the sync's own chained call does too, and an operator
  // watching this screen should see either one finish it.
  const stalledSince = useRef({ done: 0, at: 0 });
  useEffect(() => {
    if (!cycle || !data) return;
    if (done >= cycle.expected || data.active_pending === 0) {
      setCycle(null);
      return;
    }
    const now = Date.now();
    if (done !== stalledSince.current.done) {
      stalledSince.current = { done, at: now };
    } else if (now - stalledSince.current.at > STALL_MS) {
      setCycle(null);
    }
  }, [cycle, data, done]);

  useEffect(() => {
    const timer = setInterval(reload, cycle ? REFRESH_RUNNING_MS : REFRESH_MS);
    return () => clearInterval(timer);
  }, [reload, cycle]);

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{t('compliance.heading')}</h1>
          <p className="muted small">{t('compliance.subtitle')}</p>
        </div>
        <div className="head-actions">
          {/* The count is on the button because it is the one number that
              changes what pressing it means: it reads the tenders this
              deployment's layer set has not read, so "0" is a button that would
              do nothing rather than a button that is broken. No `limit` is
              sent — the server bounds the batch by the same `batch_size` this
              count was derived from, so the label states what will happen
              rather than a second opinion about it. */}
          <ActionButton
            label={
              thisPress > 0
                ? t('compliance.runNowCount', { count: thisPress })
                : t('compliance.runNowIdle')
            }
            disabled={thisPress === 0}
            onRun={async () => {
              const from = data?.runs_total ?? 0;
              await triggerExtraction({});
              setCycle({ from, expected: thisPress });
              stalledSince.current = { done: 0, at: Date.now() };
              // The task is queued, not finished; the poll above is what shows
              // the result, so the message says queued rather than done.
              setTimeout(reload, 1500);
              return t('compliance.queued', { count: thisPress });
            }}
            onDone={setFeedback}
          />
          <button type="button" className="btn btn-ghost" onClick={reload}>
            {t('action.refresh')}
          </button>
        </div>
      </header>

      <FeedbackBanner feedback={feedback} />

      {/* The one thing the numbers below cannot show: that a press is still
          being worked through. Measured against this cycle's own batch, and the
          table at the bottom of the page — refreshing three times faster while
          this is up — is where each notice appears as it is finished. */}
      {cycle && (
        <div className="banner banner-good">
          <div>{t('compliance.running', { done, total: cycle.expected })}</div>
          <ProgressBar percent={(done / cycle.expected) * 100} />
          <div className="muted small">{t('compliance.runningHint')}</div>
        </div>
      )}

      {loading && !data && <BootScreen />}
      {error != null && (
        <div className="banner banner-critical">{errorMessage(error, t)}</div>
      )}

      {data && (
        <>
          {/* The switch, stated before any number — every count below means
              something different when the schedule is off. */}
          {!data.auto_extract && (
            <div className="banner banner-warning">{t('compliance.switchedOff')}</div>
          )}

          {/* Stated separately from the queue, because these are the notices
              the button will *not* pick up however many times it is pressed —
              a queue that has stopped draining with no explanation is the one
              state this screen must never leave an operator in. */}
          {data.active_stalled > 0 && (
            <div className="banner banner-warning">
              {t('compliance.stalled', { count: data.active_stalled })}
            </div>
          )}

          <div className="stat-row">
            <StatCard
              label={t('compliance.activeTenders')}
              value={formatNumber(data.active_notices)}
              hint={t('compliance.activeHint')}
            />
            <StatCard
              label={t('compliance.read')}
              value={`${formatNumber(data.active_read)} / ${formatNumber(data.active_notices)}`}
              hint={t('compliance.readHint')}
              tone={
                data.active_notices === 0
                  ? 'neutral'
                  : data.active_pending === 0
                    ? 'good'
                    : 'warning'
              }
            />
            <StatCard
              label={t('compliance.withRequirements')}
              value={formatNumber(data.active_with_requirements)}
              hint={t('compliance.withRequirementsHint')}
            />
            <StatCard
              label={t('compliance.withExperts')}
              value={formatNumber(data.active_with_experts)}
              hint={t('compliance.withExpertsHint')}
            />
            <StatCard
              label={t('compliance.spent')}
              value={`$${data.cost_usd}`}
              hint={t('compliance.spentHint')}
            />
          </div>

          <Panel title={t('compliance.configTitle')}>
            <dl className="kv-grid">
              <div>
                <dt>{t('compliance.layers')}</dt>
                <dd>
                  <code>{data.layers}</code>{' '}
                  <span className="muted small">
                    {t(data.model_available ? 'compliance.layersPaid' : 'compliance.layersFree')}
                  </span>
                </dd>
              </div>
              <div>
                <dt>{t('compliance.model')}</dt>
                <dd>{data.model ? <code>{data.model}</code> : t('compliance.noKey')}</dd>
              </div>
              <div>
                <dt>{t('compliance.batchSize')}</dt>
                <dd>{formatNumber(data.batch_size)}</dd>
              </div>
              <div>
                <dt>{t('compliance.lastRun')}</dt>
                <dd>{data.last_run_at ? formatDateTime(data.last_run_at) : t('compliance.never')}</dd>
              </div>
              <div>
                <dt>{t('compliance.runsTotal')}</dt>
                <dd>
                  {formatNumber(data.runs_total)}
                  {data.runs_failed > 0 && (
                    <span className="tone-critical">
                      {' · '}
                      {t('compliance.runsFailed', { count: data.runs_failed })}
                    </span>
                  )}
                </dd>
              </div>
            </dl>
          </Panel>

          <Panel title={t('compliance.recentTitle')}>
            {data.recent_runs.length === 0 ? (
              <p className="muted">{t('compliance.recentEmpty')}</p>
            ) : (
              <RunTable runs={data.recent_runs} />
            )}
          </Panel>
        </>
      )}
    </>
  );
}

function RunTable({ runs }: { runs: ExtractionRunRow[] }) {
  const { t, formatDateTime } = useI18n();

  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th>{t('compliance.col.notice')}</th>
            <th>{t('compliance.col.layers')}</th>
            <th>{t('compliance.col.result')}</th>
            <th>{t('compliance.col.cost')}</th>
            <th>{t('compliance.col.when')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>
                <code>{run.notice_id}</code>
                <span className="muted small cell-sub">{run.title || '—'}</span>
              </td>
              <td><code>{run.layers}</code></td>
              <td>
                {/* Three outcomes, not two. "Read it, found nothing" is the
                    normal case for most notices and must not read as a fault
                    — the whole corpus measurement rests on that distinction. */}
                {run.status === 'failed' ? (
                  <span className="tone-critical" title={run.error}>
                    {t('compliance.result.failed')}
                  </span>
                ) : run.requirements > 0 ? (
                  <span className="tone-good">
                    {t('compliance.result.found', { count: run.requirements })}
                    {run.expert_positions > 0 && (
                      <>
                        {' '}
                        <span className="muted small">
                          {t('compliance.result.experts', { count: run.expert_positions })}
                        </span>
                      </>
                    )}
                  </span>
                ) : run.expert_positions > 0 ? (
                  /* Found a team but no threshold. A real result, and the one
                     the requirements column alone would report as nothing. */
                  <span className="tone-good">
                    {t('compliance.result.expertsOnly', { count: run.expert_positions })}
                  </span>
                ) : (
                  <span className="muted">{t('compliance.result.nothing')}</span>
                )}
              </td>
              <td>${run.cost_usd}</td>
              <td className="muted small">{formatDateTime(run.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
