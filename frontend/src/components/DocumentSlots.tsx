import { useRef, useState, type DragEvent } from 'react';

import { ApiError, submitNoticeDocument } from '../api/client';
import type {
  DocumentKind,
  HeldDocument,
  NoticeContact,
  NoticeDocumentState,
} from '../api/types';
import { useI18n, type TKey } from '../i18n';
import { errorMessage } from '../lib/errors';

/**
 * The tender documents, as slots: one per kind, each either filled or waiting.
 *
 * **What was wrong with the version this replaces.** It was a form — a file
 * input, a URL box and a button — shown only when the notice had nothing
 * readable, and it never said what the notice *did* have. So a vendor could not
 * tell whether their upload was the first or the fourth, could not see that the
 * Terms of Reference was already mirrored and only the bidding document was
 * missing, and had no way to say which of the two they were handing over. The
 * backend has taken a `kind` since the endpoint existed; the interface simply
 * never asked.
 *
 * Slots fix all three at once, because a slot shows its own state. A filled one
 * names what is in it and where it came from; an empty one is a target. The
 * vendor reads the row and knows what is left to do, which is the question they
 * actually have.
 *
 * **Why these three and not the four the backend stores.** `project_doc` is a
 * kind the harvester assigns to World Bank project files it finds on its own —
 * nobody hands one over deliberately — so offering it as a slot would be
 * offering a vendor a choice that means nothing to them. Anything that is not a
 * TOR or a bidding document goes in "other", and the extractor reads all of
 * them identically: the kind decides reading *order*
 * (`pipeline._DOCUMENT_PRIORITY`) and nothing else, so a mislabelled upload
 * costs a preference rather than a result.
 *
 * The slots are shown whether or not anything is held, unlike the old panel.
 * Half the value is the sentence "we already have the TOR", and a component
 * that hid itself once that was true could never say it.
 */
/**
 * `once` marks a slot that closes as soon as it is filled.
 *
 * A tender has one Terms of Reference and one bidding document, so once we hold
 * either there is nothing left to ask for and the upload control is an
 * invitation to send a duplicate — the vendor reads an open drop zone as "still
 * missing". "Anything else" stays open because an addendum, a clarification and
 * an annex are three documents, not three versions of one.
 */
const SLOTS: { kind: DocumentKind; title: TKey; hint: TKey; once: boolean }[] = [
  { kind: 'tor', title: 'check.supply.slot.tor', hint: 'check.supply.slot.torHint', once: true },
  { kind: 'bidding', title: 'check.supply.slot.rfp', hint: 'check.supply.slot.rfpHint', once: true },
  { kind: 'other', title: 'check.supply.slot.other', hint: 'check.supply.slot.otherHint', once: false },
];

export type SupplyOutcome =
  | { kind: 'read'; found: number }
  | { kind: 'unreadable'; problem: string };

export default function DocumentSlots({
  noticeId,
  documents,
  onOutcome,
}: {
  noticeId: string;
  documents: NoticeDocumentState;
  onOutcome: (outcome: SupplyOutcome) => void;
}) {
  const { t } = useI18n();
  const [busyKind, setBusyKind] = useState<DocumentKind | null>(null);
  const [error, setError] = useState<string | null>(null);

  const held = new Map<DocumentKind, HeldDocument[]>();
  for (const document of documents.held) {
    const list = held.get(document.kind);
    if (list) list.push(document);
    else held.set(document.kind, [document]);
  }

  async function send(kind: DocumentKind, submission: { file: File } | { url: string }) {
    setBusyKind(kind);
    setError(null);
    try {
      const result = await submitNoticeDocument(noticeId, { ...submission, kind });
      onOutcome(
        result.document.readable
          ? { kind: 'read', found: result.extraction?.requirements_found ?? 0 }
          : { kind: 'unreadable', problem: result.document.problem },
      );
    } catch (rejection) {
      // The backend's own sentence wins here, unlike everywhere else in the
      // app: a rejected upload fails for a reason the vendor can act on
      // ("unsupported file type", "the file exceeds the limit"), and "the
      // request was rejected as invalid" throws that away.
      setError(
        rejection instanceof ApiError && rejection.serverMessage
          ? rejection.serverMessage
          : errorMessage(rejection, t),
      );
    } finally {
      setBusyKind(null);
    }
  }

  return (
    <section className="card supply-panel">
      <h2 className="section-title">{t('check.supply.title')}</h2>
      <p className="muted">
        {t(documents.can_extract ? 'check.supply.bodyPartial' : 'check.supply.body')}
      </p>

      <div className="slot-grid">
        {SLOTS.map((slot) => (
          <DocumentSlot
            key={slot.kind}
            title={t(slot.title)}
            hint={t(slot.hint)}
            held={held.get(slot.kind) ?? []}
            closed={slot.once}
            busy={busyKind === slot.kind}
            disabled={busyKind !== null}
            onSubmit={(submission) => send(slot.kind, submission)}
          />
        ))}
      </div>

      {error && (
        <p className="profile-error" role="alert">
          {error}
        </p>
      )}

      {/* Only where nothing is held. The backend withholds the contact once a
          document exists, so this renders itself away without being told —
          publishing a borrower's address beside a tender we can already read
          would send vendors to ask for something we have. */}
      {documents.contact && hasContact(documents.contact) && (
        <details className="supply-contact-block">
          <summary>{t('check.supply.contactTitle')}</summary>
          <ContactList contact={documents.contact} />
        </details>
      )}

      <p className="muted small">{t('check.supply.privacy')}</p>
    </section>
  );
}

/**
 * One slot: what is in it, or a place to put something.
 *
 * A drop target *and* a file picker *and* a link box, because the three are how
 * the document actually arrives. Most vendors are forwarded an email with an
 * attachment (drop or pick) and some are sent a Google Drive link (paste) —
 * the harvester already rewrites share links, so the third costs nothing and
 * covers the case where the vendor never had a file at all.
 */
function DocumentSlot({
  title,
  hint,
  held,
  closed,
  busy,
  disabled,
  onSubmit,
}: {
  title: string;
  hint: string;
  held: HeldDocument[];
  /** Accepts nothing more once filled — see `SLOTS`. */
  closed: boolean;
  busy: boolean;
  disabled: boolean;
  onSubmit: (submission: { file: File } | { url: string }) => void;
}) {
  const { t, formatNumber } = useI18n();
  const full = closed && held.length > 0;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [url, setUrl] = useState('');

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setOver(false);
    const file = event.dataTransfer.files?.[0];
    // One at a time. The endpoint takes one document per request and a loop
    // here would fire four uploads whose failures a vendor could not tell
    // apart — and the second one would run extraction over a corpus the first
    // had already changed.
    if (file && !disabled && !full) onSubmit({ file });
  }

  return (
    <div
      className={`slot ${held.length ? 'is-filled' : ''} ${over && !full ? 'is-over' : ''} ${
        busy ? 'is-busy' : ''
      }`}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <header className="slot-head">
        <h3>{title}</h3>
        {held.length > 0 && <span className="slot-badge">{t('check.supply.slot.held')}</span>}
      </header>

      {held.length > 0 ? (
        <ul className="slot-held">
          {held.map((document) => (
            <li key={document.id}>
              <span className="slot-origin">
                {t(
                  document.origin === 'client_supplied'
                    ? 'check.supply.origin.supplied'
                    : 'check.supply.origin.harvested',
                )}
              </span>
              <span className="muted small">
                {t('check.supply.slot.chars', { count: formatNumber(document.text_chars) })}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted small slot-hint">{hint}</p>
      )}

      <input
        ref={inputRef}
        type="file"
        className="slot-input"
        accept=".pdf,.docx,.txt,.md"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onSubmit({ file });
          // Cleared so choosing the same file twice fires again — a vendor who
          // uploaded a scan, saw "no text layer", and re-exported it would
          // otherwise pick an identically named file and get nothing.
          event.target.value = '';
        }}
      />

      {/* Nothing to ask for once the slot is filled and only takes one. An
          open drop zone beside a document we already hold reads as "still
          missing", which is how the same TOR got uploaded twice. */}
      {full ? (
        <p className="slot-done">{t('check.supply.slot.complete')}</p>
      ) : (
        <div className="slot-actions">
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={disabled}
            onClick={() => inputRef.current?.click()}
          >
            {t(
              busy
                ? 'check.supply.working'
                : held.length
                  ? 'check.supply.slot.replace'
                  : 'check.supply.slot.add',
            )}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={disabled}
            onClick={() => setLinkOpen((open) => !open)}
          >
            {t('check.supply.slot.link')}
          </button>
        </div>
      )}

      {linkOpen && !full && (
        <div className="slot-link">
          <input
            type="url"
            value={url}
            placeholder={t('check.supply.urlPlaceholder')}
            onChange={(event) => setUrl(event.target.value)}
          />
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={disabled || !url.trim()}
            onClick={() => {
              onSubmit({ url: url.trim() });
              setUrl('');
              setLinkOpen(false);
            }}
          >
            {t('check.supply.submit')}
          </button>
        </div>
      )}
    </div>
  );
}

/** Whether the notice published anything worth printing as a contact. */
function hasContact(contact: NoticeContact): boolean {
  return Object.values(contact).some((value) => Boolean(value));
}

function ContactList({ contact }: { contact: NoticeContact }) {
  const { t } = useI18n();
  const rows: Array<[TKey, string, boolean]> = [
    ['check.supply.contact.name', contact.name, false],
    ['check.supply.contact.organization', contact.organization, false],
    ['check.supply.contact.email', contact.email, true],
    ['check.supply.contact.phone', contact.phone, false],
    ['check.supply.contact.address', contact.address, false],
    ['check.supply.contact.web', contact.web_url, true],
  ];

  return (
    <dl className="supply-contact">
      {rows
        .filter(([, value]) => Boolean(value))
        .map(([key, value, linkable]) => (
          <div key={key}>
            <dt>{t(key)}</dt>
            <dd>
              {linkable ? (
                <a
                  href={value.startsWith('http') ? value : `mailto:${value}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {value}
                </a>
              ) : (
                value
              )}
            </dd>
          </div>
        ))}
    </dl>
  );
}
