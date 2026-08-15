import { useCallback, useState, type ReactNode } from 'react';

import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

/** Small labelled number for the page header rows. */
export function StatCard({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: 'neutral' | 'good' | 'warning' | 'critical';
}) {
  return (
    <div className={`stat-card tone-${tone}`}>
      <span className="stat-card-label">{label}</span>
      <span className="stat-card-value">{value}</span>
      {hint && <span className="stat-card-hint">{hint}</span>}
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  success: 'good',
  completed: 'good',
  running: 'info',
  pending: 'neutral',
  partial: 'warning',
  subdivided: 'info',
  failed: 'critical',
};

/** Status is never colour alone — the label is always present. */
export function StatusPill({ status }: { status: string }) {
  const { tStatus } = useI18n();
  const tone = STATUS_TONE[status] ?? 'neutral';
  return (
    <span className={`pill pill-${tone}`}>
      <span className="pill-dot" aria-hidden="true" />
      {tStatus(status)}
    </span>
  );
}

export function Panel({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>{title}</h2>
          {description && <p className="muted small">{description}</p>}
        </div>
        {actions && <div className="panel-actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export type Feedback = { tone: 'good' | 'critical'; message: string } | null;

export function FeedbackBanner({ feedback }: { feedback: Feedback }) {
  if (!feedback) return null;
  return (
    <div className={`banner banner-${feedback.tone}`} role="status">
      {feedback.message}
    </div>
  );
}

/**
 * Button for a side-effectful call: disables itself while in flight, reports
 * the outcome, and can require a confirmation click first.
 */
export function ActionButton({
  label,
  onRun,
  confirm,
  variant = 'primary',
  size,
  onDone,
  disabled = false,
}: {
  label: string;
  /** Resolves to the already-translated success message. */
  onRun: () => Promise<string>;
  confirm?: string;
  variant?: 'primary' | 'ghost';
  size?: 'sm';
  onDone?: (feedback: Feedback) => void;
  /** For an action with nothing to do — a job whose queue is already empty. */
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);

  const run = useCallback(async () => {
    if (confirm && !armed) {
      setArmed(true);
      return;
    }
    setArmed(false);
    setBusy(true);
    try {
      const message = await onRun();
      onDone?.({ tone: 'good', message });
    } catch (error) {
      onDone?.({ tone: 'critical', message: errorMessage(error, t) });
    } finally {
      setBusy(false);
    }
  }, [armed, confirm, onDone, onRun, t]);

  return (
    <button
      type="button"
      className={`btn btn-${variant} ${size ? `btn-${size}` : ''}`}
      onClick={run}
      disabled={busy || disabled}
      onBlur={() => setArmed(false)}
    >
      {busy ? t('action.working') : armed ? (confirm ?? label) : label}
    </button>
  );
}

export function ProgressBar({ percent }: { percent: number }) {
  const clamped = Math.max(0, Math.min(100, percent));
  return (
    <div
      className="progress-track"
      role="progressbar"
      aria-valuenow={Math.round(clamped)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div className="progress-fill" style={{ width: `${clamped}%` }} />
    </div>
  );
}

export function BootScreen({ message }: { message?: string }) {
  return (
    <div className="boot-screen" role="status">
      <span className="spinner" aria-hidden="true" />
      {message && <p>{message}</p>}
    </div>
  );
}
