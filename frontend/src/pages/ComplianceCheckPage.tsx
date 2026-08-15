import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  assessNotice,
  declareRequirements,
  documentFileUrl,
  fetchNoticeSource,
} from '../api/client';
import type {
  AssessedRequirement,
  ComplianceAssessment,
  ComplianceScore,
  Importance,
  MissingInput,
  NoticeDocumentState,
  NoticeSource,
  Trace,
} from '../api/types';
import DocumentSlots, { type SupplyOutcome } from '../components/DocumentSlots';
import NoticeExpertsPanel from '../components/NoticeExpertsPanel';
import ReadinessMeter from '../components/ReadinessMeter';
import SourceViewer from '../components/SourceViewer';
import { DetailSkeleton, EmptyState, ErrorState } from '../components/StateViews';
import { useAsyncData } from '../hooks/useAsyncData';
import { useI18n, type TKey } from '../i18n';
import { errorMessage } from '../lib/errors';
import { useVendorAuth } from '../auth/VendorAuth';
import { entityLabel, recordFieldLabel, scalarLabel } from '../lib/vendorProfile';

/**
 * One notice, checked against the vendor's profile.
 *
 * The page is built around the three verdicts, and the third one is why it
 * looks the way it does. `unknown` is not a soft failure and is never styled
 * as one: it gets its own colour, its own wording ("not established yet") and,
 * uniquely, a list of the values that would settle it. A vendor reading this
 * page should come away knowing either why they are out or what to type next —
 * never with the impression that we rejected them for a question nobody asked.
 *
 * The other deliberate choice is that every requirement shows its evidence. A
 * verdict is only worth anything if the vendor can see the sentence it came
 * from, disagree with it, and go and check the tender.
 *
 * The third choice is what the page does when it has nothing to say. Most
 * notices state no criteria at all, because the criteria are in a tender
 * document the notice names but does not link — so the honest page is not an
 * empty list but a way out: who to write to for that document, and somewhere
 * to put it when they send it (D17).
 */
export default function ComplianceCheckPage() {
  const { noticeId = '' } = useParams();
  const { t, formatPercent, lang } = useI18n();
  const { email, initialising } = useVendorAuth();

  // Held here rather than inside the panel because a successful submission
  // reloads the assessment, and the panel it came from is gone by the time the
  // new verdict renders — the vendor would watch their own document vanish
  // without being told it was read.
  const [outcome, setOutcome] = useState<SupplyOutcome | null>(null);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [saveError, setSaveError] = useState<unknown>(null);
  // The answer shown before the server has confirmed it. Without this the
  // button only changes colour once the round trip and the reassessment have
  // both landed, which reads as a press that did nothing.
  const [pending, setPending] = useState<Record<number, boolean | null>>({});
  // The readiness the *write* came back with. The declarations endpoint
  // recomputes and returns it, so the bar moves on the round trip that saved
  // the answer rather than on the reassessment behind it — which is the
  // difference between an indicator that answers the switch and one that
  // catches up with it.
  const [liveScore, setLiveScore] = useState<ComplianceScore | null>(null);
  // Which criterion the source pane is showing. `null` before anything is
  // pressed, and the pane then renders the document from the top — which is
  // the right first view: a vendor who has not chosen a criterion yet is
  // reading the tender, not checking one sentence of it.
  const [activeId, setActiveId] = useState<number | null>(null);

  // The vendor is the session, so the only argument is the tender. Waits for
  // the boot call: firing while `initialising` would send an anonymous request
  // and render "sign in" to somebody who already is.
  //
  // `lang` is a dependency because the criteria come back already named in the
  // reader's language — the server resolves the label so the tender page and
  // this one cannot disagree about it (see `views._parse`). The cost of that
  // choice is this refetch, which is the cheap half of the trade.
  const assessment = useAsyncData<ComplianceAssessment | null>(
    (signal) =>
      email && !initialising ? assessNotice(noticeId, signal) : Promise.resolve(null),
    [noticeId, email, initialising, lang],
  );

  // The tender itself, for the pane beside the criteria. Loaded separately
  // from the assessment and not awaited with it: it is the larger payload by
  // far — a whole document's text or its line index — and the criteria and
  // their switches must not wait on it. A page that renders its answers while
  // the source is still arriving is the correct order of operations.
  const source = useAsyncData<NoticeSource | null>(
    (signal) => (noticeId ? fetchNoticeSource(noticeId, signal) : Promise.resolve(null)),
    [noticeId],
  );

  // An optimistic answer is dropped when — and only when — the server comes
  // back agreeing with it.
  //
  // Clearing them all on any fresh assessment is what made presses appear to go
  // missing. Each toggle fires a write and then a reassessment, a vendor
  // working down twenty criteria presses the next one while the previous round
  // trip is still in the air, and those responses do not arrive in the order
  // they were sent. A reassessment that predates the newest press was then
  // wiping it, snapping that switch back to where it had been — which the
  // vendor reads, reasonably, as a press that did nothing.
  //
  // Matching per requirement fixes it without tracking request order: a stale
  // response simply does not confirm the newer answer, so the switch holds its
  // position until one does.
  useEffect(() => {
    const fresh = assessment.data;
    if (fresh == null) return;
    const declared = new Map(fresh.requirements.map((row) => [row.id, row.declared]));
    setPending((current) => {
      const next = Object.fromEntries(
        Object.entries(current).filter(
          ([id, value]) => declared.get(Number(id)) !== value,
        ),
      );
      return Object.keys(next).length === Object.keys(current).length ? current : next;
    });
    if (savingId === null) setLiveScore(null);
  }, [assessment.data, savingId]);

  if (initialising) return <DetailSkeleton />;

  // Reading what a tender asks for is public; being measured against it is
  // not, because the answer is computed from this vendor's own declarations.
  if (!email) {
    return (
      <>
        <BackLink noticeId={noticeId} />
        <EmptyState
          title={t('auth.needAccountTitle')}
          description={t('auth.needAccountBody')}
          action={
            <Link
              className="btn btn-primary"
              to="/sign-in"
              state={{ from: `/tenders/${encodeURIComponent(noticeId)}/compliance` }}
            >
              {t('auth.goToSignIn')}
            </Link>
          }
        />
      </>
    );
  }

  // Only on the first load. `reload()` after a switch is pressed sets
  // `loading` again, and returning the skeleton here replaced the whole page —
  // which collapsed its height and threw the vendor back to the top, losing
  // the row they had just answered. Keeping the stale assessment on screen
  // while the new one arrives is what makes the switch feel like a switch.
  if (assessment.loading && assessment.data == null) return <DetailSkeleton />;

  if (assessment.error != null) {
    return (
      <>
        <BackLink noticeId={noticeId} />
        <ErrorState error={assessment.error} onRetry={assessment.reload} />
      </>
    );
  }

  const data = assessment.data;
  if (!data) return null;

  // Saving reloads the assessment rather than patching the row in place: the
  // verdict, the counts and `hard_eligibility_pass` are all derived from the
  // whole set, so a locally-updated row would leave the summary above it
  // disagreeing with the card the vendor just answered.
  const declare = async (requirementId: number, satisfied: boolean | null) => {
    setSavingId(requirementId);
    setSaveError(null);
    setPending((current) => ({ ...current, [requirementId]: satisfied }));
    try {
      const saved = await declareRequirements(noticeId, [
        { requirement_id: requirementId, satisfied },
      ]);
      // The bar moves here, one response earlier than everything else on the
      // page. It is the only part of the assessment the write can return
      // cheaply, and it is the part the vendor is watching.
      setLiveScore(saved.score);
      // Reassess rather than patch the row: the verdict, the counts and
      // `hard_eligibility_pass` are all derived from the whole set, so a
      // locally-updated row would leave the summary disagreeing with the card
      // the vendor just answered.
      assessment.reload();
    } catch (error) {
      // The optimistic answer is rolled back, because a switch that kept its
      // new colour after a failed save would be telling the vendor they had
      // recorded something they had not.
      setPending((current) => {
        const next = { ...current };
        delete next[requirementId];
        return next;
      });
      // Same rollback, same reason: a bar left at the figure the failed write
      // would have produced is the switch's lie repeated one size larger.
      setLiveScore(null);
      setSaveError(error);
    } finally {
      setSavingId(null);
    }
  };

  return (
    <article className="detail">
      <BackLink noticeId={noticeId} />

      <section className="page-head">
        <div>
          <h1>{t('check.title')}</h1>
          <p className="lead">{data.notice.title}</p>
          <p className="muted small">{t('check.assessedAs', { name: data.profile.name })}</p>
        </div>
      </section>

      <SourceStrip documents={data.documents} />

      {/* Above the verdict, because it is the thing that moves. The verdict
          card answers "can I bid"; this answers "how far have I got", which is
          the question the page's fifteen switches are for. */}
      <ReadinessMeter score={liveScore ?? data.score} saving={savingId !== null} />

      <Summary data={data} />

      <OutcomeNote outcome={outcome} />

      {/* A switch that silently failed to save would be the worst version
          of this feature: the vendor would believe they had answered. */}
      {saveError != null && (
        <p className="form-error" role="alert">
          {errorMessage(saveError, t)}
        </p>
      )}

      {data.requirements.length === 0 ? (
        // Only when a document is already held. Otherwise the empty state is
        // not the news — the missing document is, and the panel below says so
        // and does something about it.
        data.documents.can_extract && (
          <EmptyState title={t('check.emptyTitle')} description={t('check.emptyBody')} />
        )
      ) : (
        /* The criteria on the left, the tender they were read from on the
           right. Side by side rather than one behind the other because the two
           are read together: a vendor answering "do we have this" is checking
           the sentence while they answer, and a viewer that replaced the list
           would make them choose which of the two to look at. The grid
           collapses to one column below the breakpoint, where the pane becomes
           a disclosure under the criterion instead. */
        <div className="compliance-split">
          <div className="requirement-list">
            {data.requirements.map((requirement) => (
              <RequirementCard
                key={requirement.id}
                requirement={
                  requirement.id in pending
                    ? { ...requirement, declared: pending[requirement.id] }
                    : requirement
                }
                saving={savingId === requirement.id}
                active={activeId === requirement.id}
                locatable={isLocatable(source.data, requirement.id)}
                onShow={() =>
                  setActiveId((current) =>
                    current === requirement.id ? null : requirement.id,
                  )
                }
                onDeclare={(satisfied) => declare(requirement.id, satisfied)}
              />
            ))}
          </div>

          <aside className="source-pane">
            <div className="source-pane-inner">
              <h2 className="section-title">{t('viewer.title')}</h2>
              {source.loading && <p className="muted small">{t('viewer.loading')}</p>}
              {source.error != null && (
                <p className="muted small">{t('viewer.problem.none')}</p>
              )}
              {source.data && <SourceViewer source={source.data} activeId={activeId} />}
            </div>
          </aside>
        </div>
      )}

      {/* Always, not only when nothing is held. Half of what this says is "we
          already have the Terms of Reference and not the bidding document",
          and the panel that hid itself the moment one document arrived could
          never say it — which is why vendors uploaded the same file twice. */}
      <DocumentSlots
        noticeId={noticeId}
        documents={data.documents}
        onOutcome={(next) => {
          setOutcome(next);
          if (next.kind === 'read') {
            assessment.reload();
            // The pane is showing a source chosen from the old set of
            // documents. A TOR arriving is exactly the event that changes
            // which one wins, so the choice is made again.
            source.reload();
          }
        }}
      />


      {/* Under the verdict, because it is what the verdict leaves a vendor
          needing. "You cannot bid, you have no Resettlement Specialist" is a
          dead end; the same sentence followed by three people who work that
          role is the next step. It renders nothing when the tender names no
          positions, so a financial-criteria tender is unaffected. */}
      <NoticeExpertsPanel noticeId={noticeId} />

      {/* The competitor block is *not* repeated here. It was, beneath the
          verdict, on the argument that "you qualify" is only half of a bidding
          decision — but the same list already sits at the foot of the notice
          the vendor arrived from, and this page has one job: say whether they
          qualify, and on what evidence. A list of other companies under that
          answer reads as part of it, which is exactly what nothing carrying no
          quote may do. It lives on the notice detail page alone. */}

      {/* Withheld rows are reported as a number and never as content: they
          measure how often extraction invents a requirement, and showing one
          to a vendor would be passing on the invention. */}
      {data.excluded.not_found > 0 && (
        <p className="muted small withheld-note">
          {t('check.withheld', { count: data.excluded.not_found })}
        </p>
      )}

      <p className="muted small">{t('check.disclaimer')}</p>
      <p className="muted small">
        {t('check.coverage')}: {formatPercent(data.coverage)}
      </p>
    </article>
  );
}

/**
 * What the criteria below were actually read out of, in one line at the top.
 *
 * The question a vendor asks before they trust any of this is "have you read
 * the real document, or only the announcement?" — and until now the answer was
 * four screens down, inside the upload panel, phrased as an upload prompt. A
 * tender whose Terms of Reference we hold and one where we hold nothing looked
 * identical at the point where the difference matters most.
 *
 * So the held documents are named here, each a link to the file itself, and the
 * absence of a Terms of Reference is stated rather than left to be inferred
 * from an empty space. Nothing is claimed about completeness: the strip says
 * what is held, and the panel further down is still where something missing
 * gets fixed.
 */
function SourceStrip({ documents }: { documents: NoticeDocumentState }) {
  const { t } = useI18n();
  const held = documents.held ?? [];
  const hasTor = held.some((document) => document.kind === 'tor');

  return (
    <section className={`source-strip ${hasTor ? 'has-tor' : 'no-tor'}`}>
      <span className="source-strip-label">{t('check.readFrom')}</span>
      {held.length === 0 ? (
        <span className="source-strip-none">{t('check.readFrom.noneHeld')}</span>
      ) : (
        <ul className="source-strip-list">
          {held.map((document) => (
            <li key={document.id}>
              <a
                href={documentFileUrl(document.id)}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t(`check.supply.slot.${SLOT_LABEL[document.kind] ?? 'other'}` as TKey)}
              </a>
            </li>
          ))}
        </ul>
      )}
      {/* Stated, not implied. "No Terms of Reference" is the single fact that
          most changes how much weight the list below deserves, and an absence
          is the one thing an interface cannot show by leaving it out. */}
      {!hasTor && <span className="source-strip-missing">{t('check.readFrom.noTor')}</span>}
    </section>
  );
}

/** Document kinds as the upload slots name them, so both read the same. */
const SLOT_LABEL: Record<string, string> = {
  tor: 'tor',
  bidding: 'rfp',
  project_doc: 'other',
  other: 'other',
};

/**
 * Whether this criterion's quote was found in the source that is open.
 *
 * `undefined` while the source is still loading, which reads the same as
 * "yes" — the button is offered and, in the rare case the quote turns out not
 * to be locatable, the pane says so when it is pressed. The alternative is
 * hiding a control that will exist a moment later, which is worse: a vendor who
 * has already looked away does not come back to check.
 */
function isLocatable(source: NoticeSource | null | undefined, id: number): boolean {
  if (!source) return true;
  const key = String(id);
  return key in source.highlights || key in source.ranges;
}

/**
 * The headline.
 *
 * `status` is read before `hard_eligibility_pass`, not after. The boolean is
 * vacuously true when nothing was extracted — an empty conjunction — so a page
 * that rendered the boolean first would tell a vendor they qualify for a
 * tender nobody has read.
 */
function Summary({ data }: { data: ComplianceAssessment }) {
  const { t } = useI18n();

  return (
    <section className={`card summary-card summary-${data.status}`}>
      <h2 className="summary-title">{t(`check.status.${data.status}` as TKey)}</h2>
      <p className="muted">{t(`check.status.${data.status}Body` as TKey)}</p>

      {data.status !== 'unrated' && (
        <>
          <dl className="fact-strip">
            <div>
              <dt>{t('check.hardGate')}</dt>
              <dd>
                {/* Three states. `null` is rendered as its own answer, never
                    folded into "does not pass". */}
                {data.hard_eligibility_pass === true && t('check.hardGate.pass')}
                {data.hard_eligibility_pass === false && t('check.hardGate.fail')}
                {data.hard_eligibility_pass === null && t('check.hardGate.pending')}
              </dd>
            </div>
            <div>
              <dt>{t('check.counts.satisfied')}</dt>
              <dd>{data.counts.satisfied}</dd>
            </div>
            <div>
              <dt>{t('check.counts.failed')}</dt>
              <dd>{data.counts.failed}</dd>
            </div>
            <div>
              <dt>{t('check.counts.unknown')}</dt>
              <dd>{data.counts.unknown}</dd>
            </div>
          </dl>
          {data.hard_eligibility_pass === null && (
            <p className="muted small">{t('check.hardGate.pendingHint')}</p>
          )}
        </>
      )}
    </section>
  );
}

function OutcomeNote({ outcome }: { outcome: SupplyOutcome | null }) {
  const { t } = useI18n();
  if (!outcome) return null;

  if (outcome.kind === 'unreadable') {
    return (
      <p className="supply-outcome supply-outcome-problem" role="status">
        {t('check.supply.unreadable', { problem: outcome.problem })}
      </p>
    );
  }
  return (
    <p className="supply-outcome" role="status">
      {outcome.found > 0
        ? t('check.supply.found', { count: outcome.found })
        : t('check.supply.foundNothing')}
    </p>
  );
}

/**
 * One criterion, compressed to what a vendor decides on.
 *
 * The first version showed everything the API returns — verdict pill, three
 * tags, the full quote, a working-out toggle — which made a page of fifteen
 * criteria unreadable and buried the only control that matters. What a vendor
 * is doing here is answering *do we have this*, fifteen times, so the answer
 * buttons lead and everything that supports the question is either one line or
 * behind a disclosure.
 *
 * The quote stays on the card rather than moving behind the disclosure with the
 * rest: it is the sentence the criterion was read out of, and a vendor
 * answering without it is answering our paraphrase. Clamped to two lines, which
 * is enough to recognise, and expandable when it is not.
 */
function RequirementCard({
  requirement,
  onDeclare,
  onShow,
  saving,
  active,
  locatable,
}: {
  requirement: AssessedRequirement;
  onDeclare: (satisfied: boolean | null) => void;
  /** Point the source pane at this criterion, or unpoint it. */
  onShow: () => void;
  saving: boolean;
  active: boolean;
  /** Whether the quote was found in the source that is open. */
  locatable: boolean;
}) {
  const { t } = useI18n();
  const [working, setWorking] = useState(false);
  const [quoteOpen, setQuoteOpen] = useState(false);
  const answered = requirement.declared !== null;

  return (
    <section
      className={`card requirement-card compact verdict-${requirement.verdict} ${
        answered ? 'is-answered' : ''
      } ${active ? 'is-active' : ''}`}
    >
      <header className="requirement-head">
        <ImportanceMark importance={requirement.importance} />
        <h3>{requirement.label}</h3>
        {/* Only the exception is worth a badge. Marking the mandatory ones
            labels almost every row and stops meaning anything; marking the
            optional ones tells a vendor which they may skip. */}
        {!requirement.is_mandatory && (
          <span className="tag tag-quiet">{t('check.preference')}</span>
        )}
      </header>

      {/* **The card reads in one language.** The criterion is stated in the
          vendor's; the tender's own sentence is the *source*, and the source
          belongs in the pane beside it, where it is shown in full and
          highlighted in place.

          It used to sit here, three grey lines under a one-line title, and it
          made every card bilingual: the Uzbek statement of the requirement was
          the small text and the English original was the bulk of what a vendor
          read. Behind a disclosure it is no less available — and one press away
          is closer than it looks, because the button beside it puts the same
          sentence on the page it came from, which is the better answer to
          "where does it say that". */}
      {requirement.evidence_quote ? (
        <div className="evidence-row">
          {locatable && (
            <button
              type="button"
              className={`btn btn-ghost btn-sm evidence-locate ${active ? 'is-active' : ''}`}
              aria-pressed={active}
              onClick={onShow}
            >
              {t(active ? 'viewer.hideInSource' : 'viewer.showInSource')}
            </button>
          )}
          <button
            type="button"
            className="btn btn-ghost btn-sm evidence-toggle"
            aria-expanded={quoteOpen}
            onClick={() => setQuoteOpen((open) => !open)}
          >
            {t(quoteOpen ? 'check.hideOriginal' : 'check.showOriginal')}
          </button>
        </div>
      ) : (
        <p className="muted small">{t('check.evidenceNone')}</p>
      )}

      {/* Verbatim, and never translated — the grounding verifier searches the
          source for this exact string (D30). Marked with its language so a
          reader knows why it is not in theirs. */}
      {quoteOpen && requirement.evidence_quote && (
        <blockquote className="evidence compact is-open" lang="en">
          {requirement.evidence_quote}
        </blockquote>
      )}

      {/* The control the page exists for, directly under the sentence it
          answers. */}
      <DeclarationSwitch
        declared={requirement.declared}
        decidedBy={requirement.decided_by}
        saving={saving}
        onChange={onDeclare}
      />

      {/* Everything an auditor needs and a vendor does not: where the row came
          from, which layer read it, and the evaluation itself. Behind one
          disclosure so the card stays a question rather than a report. */}
      <button
        type="button"
        className="btn btn-ghost btn-sm requirement-details-toggle"
        onClick={() => setWorking((open) => !open)}
        aria-expanded={working}
      >
        {t(working ? 'check.hideWorking' : 'check.showWorking')}
      </button>

      {working && (
        <div className="requirement-details">
          <div className="requirement-tags">
            <span className="tag">
              {t(requirement.is_mandatory ? 'check.mandatory' : 'check.preference')}
            </span>
            <span className="tag tag-quiet">
              {t(`check.appliesTo.${requirement.applies_to}` as TKey)}
            </span>
            <span
              className="tag tag-quiet"
              title={t(`check.grounding.${requirement.grounding}` as TKey)}
            >
              {t(`check.layer.${requirement.layer}` as TKey)}
            </span>
          </div>

          <p className="muted small">
            {t('check.evidence')}
            {requirement.source && ` · ${requirement.source}`}
            {requirement.source_document && (
              <>
                {' · '}
                <a
                  href={requirement.source_document.url}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('check.source')}
                </a>
              </>
            )}
          </p>

          {/* Still offered, and still only where the vendor has not answered:
              asking for a turnover figure under a criterion they have already
              settled is noise. */}
          {requirement.declared === null && requirement.missing.length > 0 && (
            <MissingPanel missing={requirement.missing} />
          )}

          {/* The trace, not a retelling of it. This is the evaluation the
              engine actually performed, which is what makes the verdict
              checkable. */}
          <TraceTree trace={requirement.trace} />
        </div>
      )}
    </section>
  );
}

/**
 * What this criterion decides, as a mark the eye can sort on.
 *
 * A dot and a word rather than a colour alone: the list is already ordered by
 * importance, so the mark's job is to make that order legible — a vendor
 * scrolling past the gates and into the preferences should be able to see where
 * the boundary was without counting.
 *
 * Rendered for `high` and `low` only. `medium` is the default level and the
 * bulk of every list, so marking it would put a badge on almost every row and
 * leave the two that matter no more visible than before. Unjudged rows are
 * blank for the same reason they weigh as medium: nobody read them either way,
 * and inventing a mark would be claiming otherwise.
 */
function ImportanceMark({ importance }: { importance: Importance }) {
  const { t } = useI18n();
  if (importance !== 'high' && importance !== 'low') return null;

  return (
    <span className={`importance-mark importance-${importance}`}>
      <span className="importance-dot" aria-hidden="true" />
      {t(`check.importance.${importance}` as TKey)}
    </span>
  );
}

/**
 * The vendor's answer, as a two-position switch the size of a phone's.
 *
 * **Two positions, after a three-position version was tried and rejected.** The
 * middle rest position expressed "not answered yet" exactly and cost more than
 * it was worth: at three times the width it dominated a card whose whole job is
 * one question, and the two dead zones either side of the knob swallowed
 * presses that landed a few pixels off. A switch you have to aim at is not a
 * switch.
 *
 * So the control is off/on and the *track colour and the words* carry the third
 * state instead: grey and "not answered yet" before the vendor touches it, red
 * and "we do not" once they have said no, green and "we have this" for yes.
 * Nothing is lost from the engine — an untouched row is still `null`, still
 * UNKNOWN, and still never collapses into a failure (D3). What changed is that
 * the distinction is read rather than aimed at.
 *
 * Withdrawing an answer moved to a quiet link, shown only once there is
 * something to withdraw. It is rare — a vendor corrects an answer far more
 * often than they un-answer one — and it was paying for a third of the control.
 *
 * **Never disabled while saving.** It used to be, and that is what made presses
 * go missing: every toggle fires a write *and* a reassessment, and a vendor
 * working down twenty criteria clicks the next one long before both land. The
 * position now follows the optimistic answer, a second press supersedes the
 * first, and the server's last write wins — which is also the vendor's last
 * press.
 */
function DeclarationSwitch({
  declared,
  decidedBy,
  saving,
  onChange,
}: {
  declared: boolean | null;
  decidedBy: 'declaration' | 'engine';
  saving: boolean;
  onChange: (satisfied: boolean | null) => void;
}) {
  const { t } = useI18n();
  const position = declared === true ? 'yes' : declared === false ? 'no' : 'unset';

  return (
    <div className="declaration">
      <p className="declaration-question">{t('check.declare.question')}</p>

      <div className={`switch switch-${position} ${saving ? 'is-saving' : ''}`}>
        <button
          type="button"
          className="switch-track"
          role="switch"
          aria-checked={declared === true}
          aria-label={t('check.declare.question')}
          onClick={() => onChange(declared !== true)}
        >
          <span className="switch-knob" aria-hidden="true" />
        </button>

        {/* The answer in words. A knob position is fast to scan and ambiguous
            on its own, and this is the line a vendor re-reads when they come
            back to check what they claimed. */}
        <span className="switch-state">{t(`check.declare.state.${position}` as TKey)}</span>

        {declared !== null && (
          <button
            type="button"
            className="switch-clear"
            onClick={() => onChange(null)}
          >
            {t('check.declare.clear')}
          </button>
        )}
      </div>

      {decidedBy === 'declaration' && (
        <p className="muted small declaration-note">{t('check.declare.byYou')}</p>
      )}
    </div>
  );
}

/** What the vendor could tell us to turn an unknown into an answer. */
function MissingPanel({ missing }: { missing: MissingInput[] }) {
  const { t } = useI18n();

  return (
    <div className="missing-panel">
      <p className="missing-title">{t('check.missingTitle')}</p>
      <ul className="missing-list">
        {missing.map((entry, index) => (
          <li key={index}>{describeMissing(entry, t)}</li>
        ))}
      </ul>
      <Link className="btn btn-ghost btn-sm" to="/profile">
        {t('check.goToProfile')}
      </Link>
    </div>
  );
}

/**
 * Name a missing input in the vendor's own vocabulary where possible.
 *
 * The profile form's label wins over the label extraction produced, because
 * the vendor is being sent back to that form to fill it in and the two should
 * read the same. The raw key is the last fallback — a tender can require
 * something the form has no box for, and saying so is more honest than
 * dropping the row.
 */
function describeMissing(
  entry: MissingInput,
  t: (key: TKey, params?: Record<string, string | number>) => string,
): string {
  if (entry.kind === 'collection' && entry.entity) {
    return t('check.missing.collection', { label: entityLabel(entry.entity, t) });
  }
  if (entry.kind === 'record_field' && entry.entity && entry.field) {
    return t('check.missing.recordField', {
      field: recordFieldLabel(entry.entity, entry.field, t),
      entity: entityLabel(entry.entity, t),
    });
  }
  const key = entry.key ?? '';
  const known = scalarLabel(key, t);
  return t('check.missing.scalar', {
    label: known !== key ? known : entry.label || key,
  });
}

function TraceTree({ trace }: { trace: Trace }) {
  return (
    <ul className="trace">
      <TraceNode trace={trace} />
    </ul>
  );
}

function TraceNode({ trace }: { trace: Trace }) {
  return (
    <li className={`trace-node verdict-${trace.verdict}`}>
      <span className="trace-detail">{trace.detail || trace.node}</span>
      {trace.children.length > 0 && (
        <ul className="trace">
          {trace.children.map((child, index) => (
            <TraceNode key={index} trace={child} />
          ))}
        </ul>
      )}
    </li>
  );
}

function BackLink({ noticeId }: { noticeId: string }) {
  const { t } = useI18n();
  return (
    <Link to={`/tenders/${encodeURIComponent(noticeId)}`} className="back-link">
      <span aria-hidden="true">←</span> {t('check.openTender')}
    </Link>
  );
}
