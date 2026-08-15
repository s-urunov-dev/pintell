import { Link } from 'react-router-dom';

import type { ContactTier, NoticeContact, NoticeContacts } from '../api/types';
import { useI18n, type MessageKey } from '../i18n';

interface ContactsPanelProps {
  contacts: NoticeContacts;
  /** Pre-fills the subject line of any mail opened from this panel. */
  noticeTitle: string;
}

/**
 * Everyone reachable about a notice, in three tiers.
 *
 * The tiers are ranked by how strong their source is, not by seniority — see
 * `apps/tenders/contacts.py` for why the data cannot support a role ranking.
 * That ordering has to survive contact with the UI, so each tier is drawn
 * differently rather than merely stacked:
 *
 * 1. **Published contact** — the notice's own fields. Full card, mail button.
 * 2. **Also in the notice text** — parsed out of prose, so it carries the
 *    borrower's wording as a tooltip and a note that it was read, not
 *    published as a field. It is not decoration: on more than half the live
 *    notices this tier holds the address bids are actually delivered to, which
 *    is why it sits above the Bank rather than in a footnote.
 * 3. **World Bank team** — names the Bank publishes for the project. Muted,
 *    last, and explicit that it is not the tender's contact: writing here
 *    first is both useless and, under the Procurement Regulations the notices
 *    cite, the wrong channel.
 *
 * An empty tier is omitted by the backend; an empty panel renders nothing.
 */
export default function ContactsPanel({ contacts, noticeTitle }: ContactsPanelProps) {
  const { t } = useI18n();

  if (!contacts?.groups?.length) return null;

  return (
    <section className="side-block contacts-block">
      <h2 className="side-title">{t('contacts.title')}</h2>

      {contacts.groups.map((group) => (
        <div key={group.tier} className={`contact-tier contact-tier-${group.tier}`}>
          <header className="contact-tier-head">
            <span className="contact-tier-rank" aria-hidden="true">
              {group.priority}
            </span>
            <div>
              <h3>{t(TIER_TITLE[group.tier])}</h3>
              <p className="muted small">{t(TIER_HINT[group.tier])}</p>
            </div>
          </header>

          <ul className="contact-cards">
            {group.contacts.map((contact, index) => (
              <li key={contactKey(contact, index)}>
                <ContactCard
                  contact={contact}
                  tier={group.tier}
                  noticeTitle={noticeTitle}
                />
              </li>
            ))}
          </ul>

          {group.tier === 'body' && <p className="muted small">{t('contacts.parsedNote')}</p>}
        </div>
      ))}
    </section>
  );
}

const TIER_TITLE: Record<ContactTier, MessageKey> = {
  notice: 'contacts.tier.notice',
  body: 'contacts.tier.body',
  bank: 'contacts.tier.bank',
};

const TIER_HINT: Record<ContactTier, MessageKey> = {
  notice: 'contacts.tier.noticeHint',
  body: 'contacts.tier.bodyHint',
  bank: 'contacts.tier.bankHint',
};

const PURPOSE_LABEL: Record<string, MessageKey> = {
  submission: 'contacts.purpose.submission',
  enquiry: 'contacts.purpose.enquiry',
  tor: 'contacts.purpose.tor',
};

const LINK_LABEL: Record<string, MessageKey> = {
  worldbank: 'contacts.profileLink',
  publication: 'contacts.publicationLink',
  profile: 'contacts.profileLink',
};

/** Stable across re-renders without assuming an address exists. */
function contactKey(contact: NoticeContact, index: number): string {
  return contact.email || contact.name || `contact-${index}`;
}

/** Borrowers publish bare hosts as often as full URLs. */
function websiteHref(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}


function ContactCard({
  contact,
  tier,
  noticeTitle,
}: {
  contact: NoticeContact;
  tier: ContactTier;
  noticeTitle: string;
}) {
  const { t } = useI18n();
  const subject = encodeURIComponent(t('tor.mailSubject', { title: noticeTitle }));

  return (
    <article className="contact-card">
      <div className="contact-card-head">
        {contact.name ? (
          <strong className="contact-name">{contact.name}</strong>
        ) : (
          <span className="muted small contact-name">{t('contacts.unnamed')}</span>
        )}
        {contact.purpose && PURPOSE_LABEL[contact.purpose] && (
          <span className={`contact-purpose purpose-${contact.purpose}`}>
            {t(PURPOSE_LABEL[contact.purpose])}
          </span>
        )}
      </div>

      {(contact.role || contact.organization) && (
        <p className="muted small contact-role">
          {[contact.role, contact.organization].filter(Boolean).join(' · ')}
        </p>
      )}

      {contact.same_as_primary && (
        <p className="muted small contact-note">{t('contacts.samePerson')}</p>
      )}

      {contact.email ? (
        <p className="contact-line">
          {/* The borrower's own sentence around the address, so a parsed
              value can be judged before it is trusted. */}
          <a href={`mailto:${contact.email}?subject=${subject}`} title={contact.context || undefined}>
            {contact.email}
          </a>
          {tier === 'bank' && (
            <span
              className={`contact-flag ${contact.email_confirmed ? 'is-confirmed' : 'is-derived'}`}
            >
              {/* Three states, not two, because "confirmed" is not a
                  provenance. An address out of the project's ESRS was printed
                  in a World Bank document about *this* project; one found on a
                  staff page was published about the person; one derived from
                  the address pattern was published by nobody. A reader
                  deciding whether to write to it is deciding between those. */}
              {t(
                contact.source === 'project_esrs'
                  ? 'contacts.emailFromEsrs'
                  : contact.email_confirmed
                    ? 'contacts.emailConfirmed'
                    : 'contacts.emailUnconfirmed',
              )}
            </span>
          )}
        </p>
      ) : (
        tier === 'bank' && <p className="muted small contact-line">{t('contacts.noEmail')}</p>
      )}

      {contact.alternate_emails.length > 0 && (
        <p className="muted small contact-line">
          {t('contacts.alsoAt')}{' '}
          {contact.alternate_emails.map((email, index) => (
            <span key={email}>
              {index > 0 && ', '}
              <a href={`mailto:${email}?subject=${subject}`}>{email}</a>
            </span>
          ))}
        </p>
      )}

      {contact.phone && (
        <p className="contact-line">
          <a href={`tel:${contact.phone.replace(/[^\d+]/g, '')}`}>{contact.phone}</a>
        </p>
      )}

      {(contact.address || contact.country) && (
        <p className="muted small contact-line">
          {[contact.address, contact.country].filter(Boolean).join(', ')}
        </p>
      )}

      {contact.summary && <p className="muted small contact-line">{contact.summary}</p>}

      {/* Only when a profile exists — the page is built from stored data, so
          a name nobody has looked up has nothing to open. */}
      {contact.profile_id && (
        <p className="contact-line">
          <Link to={`/team-leads/${contact.profile_id}`}>{t('lead.viewProfile')}</Link>
        </p>
      )}

      {contact.links.length > 0 && (
        <p className="contact-line contact-links">
          {contact.links.map((link) => (
            <a
              key={link.url}
              href={link.url}
              target="_blank"
              rel="noopener noreferrer nofollow"
            >
              {t(LINK_LABEL[link.kind] ?? 'contacts.otherLink')}
            </a>
          ))}
        </p>
      )}

      {contact.website && !contact.links.length && (
        <p className="contact-line">
          {/* A borrower's site is worth reading as an address; a staff profile
              page is an opaque URL, so that one gets a label instead. */}
          <a href={websiteHref(contact.website)} target="_blank" rel="noopener noreferrer nofollow">
            {tier === 'bank' ? t('contacts.profileLink') : contact.website}
          </a>
        </p>
      )}
    </article>
  );
}
