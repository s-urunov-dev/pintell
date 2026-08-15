import { useCallback, useEffect, useState } from 'react';

import { Navigate } from 'react-router-dom';

import { updateMyProfile } from '../api/client';
import { useVendorAuth } from '../auth/VendorAuth';
import { DetailSkeleton } from '../components/StateViews';
import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';
import {
  COLLECTIONS,
  SCALAR_FIELDS,
  emptyDraft,
  draftToPayload,
  payloadToDraft,
  type CollectionSpec,
  type DraftRecord,
  type ProfileDraft,
  type ProfileField,
} from '../lib/vendorProfile';

/**
 * What the vendor declares about itself.
 *
 * The form is built around one rule, and it is the reason several obvious
 * shortcuts are not taken here: **an empty field is a question, not a "no"**.
 * So nothing is marked required, no field is validated against a threshold,
 * and the page never scores or grades what has been entered. A profile that is
 * a quarter filled in is a normal state — it produces "not established yet"
 * verdicts, which is the correct answer, not an error to be nagged about.
 *
 * The one place that needs more than a text box is a record type the vendor
 * has not touched. "I have no completed contracts" and "I have not told you
 * about my contracts" are different answers — the first is a real failure, the
 * second leaves the question open — so the interface offers both explicitly
 * rather than guessing which an empty section means.
 */
export default function VendorProfilePage() {
  const { t } = useI18n();
  const { profile, initialising, setProfile } = useVendorAuth();

  const [draft, setDraft] = useState<ProfileDraft>(emptyDraft);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [nameMissing, setNameMissing] = useState(false);

  // The profile arrives with the session, so there is nothing to fetch here.
  // Seeded once per profile object rather than on every render: keying on the
  // object means an in-progress edit is never overwritten by a re-render.
  useEffect(() => {
    if (profile) setDraft(payloadToDraft(profile));
  }, [profile]);

  const patch = useCallback((change: Partial<ProfileDraft>) => {
    setDraft((current) => ({ ...current, ...change }));
    setSaved(false);
  }, []);

  const setScalar = useCallback(
    (key: string, value: string) => {
      setDraft((current) => ({
        ...current,
        scalars: { ...current.scalars, [key]: value },
      }));
      setSaved(false);
    },
    [],
  );

  const setRecords = useCallback((entity: string, rows: DraftRecord[] | undefined) => {
    setDraft((current) => {
      const collections = { ...current.collections };
      if (rows === undefined) delete collections[entity];
      else collections[entity] = rows;
      return { ...current, collections };
    });
    setSaved(false);
  }, []);

  const save = useCallback(async () => {
    if (!draft.name.trim()) {
      setNameMissing(true);
      return;
    }
    setNameMissing(false);
    setSaving(true);
    setSaveError(null);
    try {
      const result = await updateMyProfile(draftToPayload(draft));
      setProfile(result);
      // Re-seed from the response rather than keeping the local draft: the
      // backend drops blank values, and showing what was actually stored is
      // the only way the vendor can tell what we hold about them.
      setDraft(payloadToDraft(result));
      setSaved(true);
    } catch (error) {
      setSaveError(error);
    } finally {
      setSaving(false);
    }
  }, [draft, setProfile]);

  if (initialising) return <DetailSkeleton />;

  // A profile belongs to an account, so there is nothing to show without one.
  // `replace` keeps the back button working: the visitor came from a tender,
  // and that is where signing in should return them.
  if (!profile) return <Navigate to="/sign-in" replace state={{ from: '/profile' }} />;

  return (
    <article className="profile-page">
      <section className="page-head">
        <div>
          <h1>{t('profile.title')}</h1>
          <p className="lead">{t('profile.lead')}</p>
        </div>
      </section>

      <section className="card">
        <h2 className="section-title">{t('profile.identity')}</h2>
        <div className="filter-row">
          <div className="field field-grow">
            <label htmlFor="profile-name">{t('profile.field.name')}</label>
            <input
              id="profile-name"
              type="text"
              value={draft.name}
              onChange={(event) => patch({ name: event.target.value })}
              autoComplete="organization"
            />
            {nameMissing && <p className="field-note">{t('profile.nameRequired')}</p>}
          </div>
          <div className="field">
            <label htmlFor="profile-country">{t('profile.field.country')}</label>
            <input
              id="profile-country"
              type="text"
              value={draft.country}
              onChange={(event) => patch({ country: event.target.value })}
              autoComplete="country-name"
            />
          </div>
        </div>
      </section>

      <section className="card">
        <h2 className="section-title">{t('profile.financials')}</h2>
        <p className="muted small">{t('profile.financials.hint')}</p>
        <div className="filter-row">
          {SCALAR_FIELDS.map((field) => (
            <div className="field field-grow" key={field.key}>
              <FieldLabel field={field} htmlFor={`scalar-${field.key}`} />
              <ValueInput
                id={`scalar-${field.key}`}
                field={field}
                value={draft.scalars[field.key] ?? ''}
                onChange={(value) => setScalar(field.key, value)}
              />
            </div>
          ))}
        </div>
      </section>

      {COLLECTIONS.map((spec) => (
        <CollectionSection
          key={spec.entity}
          spec={spec}
          rows={draft.collections[spec.entity]}
          onChange={(rows) => setRecords(spec.entity, rows)}
        />
      ))}

      <div className="profile-actions">
        <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
          {saving ? t('profile.saving') : t('profile.save')}
        </button>
        {saved && <span className="profile-saved">{t('profile.saved')}</span>}
        {saveError != null && (
          <span className="profile-error" role="alert">
            {errorMessage(saveError, t)}
          </span>
        )}
        <p className="muted small">{t('profile.reassure')}</p>
      </div>
    </article>
  );
}

/**
 * One record type — contracts, experts, certificates.
 *
 * Three states, and the difference between the first two is the whole reason
 * this component is not just a list: `undefined` means nothing has been said,
 * `[]` means "I have none of these", and the engine reads them differently.
 */
function CollectionSection({
  spec,
  rows,
  onChange,
}: {
  spec: CollectionSpec;
  rows: DraftRecord[] | undefined;
  onChange: (rows: DraftRecord[] | undefined) => void;
}) {
  const { t } = useI18n();
  const records = rows ?? [];

  return (
    <section className="card">
      <h2 className="section-title">{t(spec.titleKey)}</h2>
      <p className="muted small">{t(spec.hintKey)}</p>

      {rows === undefined && <p className="profile-state">{t('profile.notDeclared')}</p>}
      {rows !== undefined && rows.length === 0 && (
        <p className="profile-state">{t('profile.declaredNone')}</p>
      )}

      {records.map((record, index) => (
        <fieldset className="profile-record" key={index}>
          <div className="filter-row">
            {spec.fields.map((field) => (
              <div className="field field-grow" key={field.key}>
                <FieldLabel field={field} htmlFor={`${spec.entity}-${index}-${field.key}`} />
                <ValueInput
                  id={`${spec.entity}-${index}-${field.key}`}
                  field={field}
                  value={record[field.key] ?? ''}
                  onChange={(value) =>
                    onChange(
                      records.map((row, position) =>
                        position === index ? { ...row, [field.key]: value } : row,
                      ),
                    )
                  }
                />
              </div>
            ))}
          </div>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => onChange(records.filter((_, position) => position !== index))}
          >
            {t('profile.remove')}
          </button>
        </fieldset>
      ))}

      <div className="profile-record-actions">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => onChange([...records, {}])}
        >
          {t(spec.addKey)}
        </button>
        {rows === undefined ? (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange([])}>
            {t('profile.declareNone')}
          </button>
        ) : (
          records.length === 0 && (
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => onChange(undefined)}
            >
              {t('profile.undeclare')}
            </button>
          )
        )}
      </div>
    </section>
  );
}

function FieldLabel({ field, htmlFor }: { field: ProfileField; htmlFor: string }) {
  const { t } = useI18n();
  return (
    <label htmlFor={htmlFor}>
      {t(field.labelKey)}
      {/* A name that has not been reconciled with tender wording is marked
          rather than hidden: if extraction ends up using a different key, this
          is the field that will silently stop matching. */}
      {field.provisional && (
        <span className="field-provisional" title={t('profile.provisional')}>
          {' '}
          ?
        </span>
      )}
    </label>
  );
}

function ValueInput({
  id,
  field,
  value,
  onChange,
}: {
  id: string;
  field: ProfileField;
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useI18n();

  if (field.kind === 'boolean') {
    // Three options, not a checkbox. A checkbox has no way to say "I have not
    // answered", and defaulting it to unticked would declare "no" on the
    // vendor's behalf — exactly the coercion the engine exists to prevent.
    return (
      <select id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">{t('profile.boolUnset')}</option>
        <option value="true">{t('profile.boolYes')}</option>
        <option value="false">{t('profile.boolNo')}</option>
      </select>
    );
  }

  return (
    <input
      id={id}
      type="text"
      inputMode={field.kind === 'text' ? 'text' : 'numeric'}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      autoComplete="off"
    />
  );
}
