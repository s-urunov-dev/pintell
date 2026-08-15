import type { ReactNode } from 'react';

import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

interface ErrorStateProps {
  /** The rejection itself — translated here, at render time. */
  error: unknown;
  onRetry?: () => void;
}

export function ErrorState({ error, onRetry }: ErrorStateProps) {
  const { t } = useI18n();

  return (
    <div className="state-panel state-error" role="alert">
      <span className="state-icon" aria-hidden="true">!</span>
      <div>
        <h3>{t('state.errorTitle')}</h3>
        <p className="muted">{errorMessage(error, t)}</p>
      </div>
      {onRetry && (
        <button type="button" className="btn btn-primary" onClick={onRetry}>
          {t('state.tryAgain')}
        </button>
      )}
    </div>
  );
}

interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ title, description, action }: EmptyStateProps) {
  return (
    <div className="state-panel">
      <span className="state-icon" aria-hidden="true">∅</span>
      <div>
        <h3>{title}</h3>
        {description && <p className="muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function CardSkeleton() {
  return (
    <article className="card skeleton-card" aria-hidden="true">
      <div className="skeleton skeleton-line" style={{ width: '30%' }} />
      <div className="skeleton skeleton-line" style={{ width: '85%', height: 20 }} />
      <div className="skeleton skeleton-line" style={{ width: '60%' }} />
      <div className="skeleton-meta">
        <div className="skeleton skeleton-pill" />
        <div className="skeleton skeleton-pill" />
        <div className="skeleton skeleton-pill" />
      </div>
    </article>
  );
}

export function ListSkeleton({ count = 6 }: { count?: number }) {
  const { t } = useI18n();
  return (
    <div
      className="card-grid"
      role="status"
      aria-live="polite"
      aria-label={t('state.loadingTenders')}
    >
      {Array.from({ length: count }, (_, index) => (
        <CardSkeleton key={index} />
      ))}
    </div>
  );
}

export function DetailSkeleton() {
  const { t } = useI18n();
  return (
    <div className="detail-skeleton" role="status" aria-label={t('state.loadingTender')}>
      <div className="skeleton skeleton-line" style={{ width: '20%' }} />
      <div className="skeleton skeleton-line" style={{ width: '70%', height: 32 }} />
      <div className="skeleton skeleton-line" style={{ width: '45%' }} />
      <div className="skeleton skeleton-block" />
    </div>
  );
}
