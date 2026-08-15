import type { TorInfo } from '../api/types';
import { useI18n } from '../i18n';

interface TorPanelProps {
  tor: TorInfo;
  /** Used to pre-fill the subject line when the TOR has to be requested. */
  noticeTitle: string;
}

/**
 * How to get the Terms of Reference.
 *
 * The TOR is the document a bidder actually needs — the notice body is a
 * summary, the TOR carries the scope and the qualification bar. Neither
 * upstream feed publishes it, so the backend reads it out of the notice text
 * and the answer comes in three strengths: a link, an address to request it
 * from, or only the knowledge that one exists. All three are worth showing;
 * the last two at least stop the user hunting through the body for a link
 * that was never there.
 */
export default function TorPanel({ tor, noticeTitle }: TorPanelProps) {
  const { t } = useI18n();

  const torLink = tor.links.find((link) => link.kind === 'tor');
  const otherLinks = tor.links.filter((link) => link !== torLink);

  // Nothing found and nothing claimed — no panel rather than an empty one.
  if (!torLink && !tor.email && !tor.mentioned && otherLinks.length === 0) return null;

  return (
    <section className="side-block tor-block">
      <h2 className="side-title">{t('tor.title')}</h2>

      {torLink ? (
        <>
          <a
            className="btn btn-primary btn-block tor-open"
            href={torLink.url}
            target="_blank"
            rel="noopener noreferrer"
            // The borrower's own wording, so the user can judge the link
            // before opening a Google Drive id that says nothing by itself.
            title={torLink.context || undefined}
          >
            {t('tor.open')}
          </a>
          <p className="muted small tor-source">{t('tor.fromNotice')}</p>
        </>
      ) : tor.email ? (
        <>
          <a
            className="btn btn-primary btn-block"
            href={`mailto:${tor.email}?subject=${encodeURIComponent(
              t('tor.mailSubject', { title: noticeTitle }),
            )}`}
          >
            {t('tor.request')}
          </a>
          <p className="muted small tor-source">{t('tor.requestHint', { email: tor.email })}</p>
        </>
      ) : tor.mentioned ? (
        <p className="muted small tor-source">{t('tor.mentionedOnly')}</p>
      ) : null}

      {otherLinks.length > 0 && (
        <ul className="tor-links">
          {otherLinks.map((link) => (
            <li key={link.url}>
              <a href={link.url} target="_blank" rel="noopener noreferrer" title={link.context || undefined}>
                {t(link.kind === 'bidding' ? 'tor.biddingDocument' : 'tor.otherLink')}
              </a>
              <span className="tor-host">{hostOf(link.url)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The domain alone — the path is an opaque id and only adds noise. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}
