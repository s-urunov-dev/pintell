import { useI18n, type TKey } from '../i18n';
import type { ComplianceScore } from '../api/types';

/**
 * How ready this bid is, as one bar that answers the switch below it.
 *
 * The bar has **two** filled segments, and that is the whole design. The solid
 * one is what the vendor has established; the ghosted one runs on to the
 * ceiling — what they would reach by settling everything still unknown. The
 * remainder is what has already been lost and cannot be recovered by answering
 * anything, so it stays empty however many switches are pressed.
 *
 * A single-segment bar was the obvious version and it lies in both directions
 * at once. Filling it to the ceiling tells a bidder with a failed mandatory
 * criterion that they are nearly there; filling it only to the score tells a
 * bidder who has answered nothing that they have failed. The gap between the
 * two segments is exactly the information a vendor is missing, so it is drawn
 * rather than averaged away.
 *
 * `blocked` overrides the colour but never the number. A bid can be most of the
 * way established and impossible — the percentage is a summary of verdicts, and
 * the block is a verdict — so the bar goes red and still reads 85%, with the
 * sentence under it saying why those are both true.
 *
 * The width is animated by CSS rather than by a counter here, so the movement
 * belongs to the state change: the vendor presses a switch, the server returns
 * the recomputed figure, the bar travels to it. Nothing on this side computes a
 * percentage — the arithmetic is weighted by an importance the client cannot
 * see, and a second implementation of it would drift from the one that counts.
 */
export default function ReadinessMeter({
  score,
  saving,
}: {
  score: ComplianceScore;
  /** A write is in flight. The bar is a moment stale and says so, quietly. */
  saving?: boolean;
}) {
  const { t, formatPercent } = useI18n();

  // Nothing has been extracted, so there is nothing to be a fraction of. The
  // vacuous 100% is arithmetically defensible and would be the most misleading
  // thing on the page, and 0% would read as a failure nobody has judged.
  if (score.total === 0) return null;

  const state = score.blocked ? 'blocked' : score.score >= 1 ? 'complete' : 'open';
  const remaining = Math.max(score.ceiling - score.score, 0);

  return (
    <section
      className={`card readiness readiness-${state} ${saving ? 'is-saving' : ''}`}
      aria-live="polite"
    >
      <header className="readiness-head">
        <div>
          <p className="readiness-caption">{t('check.readiness.title')}</p>
          <p className="readiness-value">
            <strong>{formatPercent(score.score)}</strong>
            {remaining > 0.001 && (
              <span className="readiness-ceiling">
                {t('check.readiness.ceiling', {
                  value: formatPercent(score.ceiling),
                })}
              </span>
            )}
          </p>
        </div>
        <p className="readiness-counts">
          {t('check.readiness.counts', {
            satisfied: score.counts.satisfied,
            total: score.counts.total,
          })}
        </p>
      </header>

      {/* One track, two fills. The ghost is drawn first and the solid one over
          it, so the two are read as one bar with a lighter tail rather than as
          two bars that happen to touch. */}
      <div
        className="readiness-track"
        role="progressbar"
        aria-valuenow={Math.round(score.score * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={t('check.readiness.title')}
      >
        <div
          className="readiness-fill readiness-fill-open"
          style={{ width: `${score.ceiling * 100}%` }}
        />
        <div
          className="readiness-fill readiness-fill-earned"
          style={{ width: `${score.score * 100}%` }}
        />
      </div>

      <p className="readiness-note muted small">
        {score.blocked
          ? t('check.readiness.blocked')
          : remaining > 0.001
            ? t('check.readiness.open')
            : t('check.readiness.settled')}
      </p>

      {/* Where the missing weight sits. "You have answered the formalities and
          none of the gates" is the sentence the bar itself cannot say, and it
          is the one that tells a vendor what to do next. */}
      <ImportanceBreakdown score={score} />
    </section>
  );
}

/**
 * The same fraction, split by what each criterion decides.
 *
 * Levels with no criteria are omitted rather than shown at zero: a tender that
 * states no preferences should not display an empty "preferences" row implying
 * the vendor has missed something.
 */
function ImportanceBreakdown({ score }: { score: ComplianceScore }) {
  const { t, formatPercent } = useI18n();
  const levels = (['high', 'medium', 'low'] as const).filter(
    (level) => (score.by_importance[level]?.count ?? 0) > 0,
  );
  if (levels.length < 2) return null;

  return (
    <dl className="readiness-split">
      {levels.map((level) => {
        const weights = score.by_importance[level];
        return (
          <div key={level} className={`readiness-split-item importance-${level}`}>
            <dt>{t(`check.importance.${level}` as TKey)}</dt>
            <dd>
              {weights.total > 0 ? formatPercent(weights.earned / weights.total) : '—'}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}
