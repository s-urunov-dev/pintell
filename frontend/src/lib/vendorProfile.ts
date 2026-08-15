import type { TKey } from '../i18n';
import type { VendorProfileInput } from '../api/types';
import { PROFILE_KEY } from './storage';

/**
 * The shape of the vendor profile form.
 *
 * These descriptors exist because the *key names* matter more than the layout:
 * a profile stores `annual_turnover_avg`, and a requirement extracted from a
 * tender asks about `annual_turnover_avg`. If the two spellings drift apart
 * nothing matches, and every criterion comes back unknown forever — a silent
 * failure that looks exactly like an empty profile. Keeping the names in one
 * table makes the coupling visible instead of scattering it through JSX.
 *
 * **Where each name comes from.** The backend deliberately validates shape and
 * not vocabulary (`apps/compliance/serializers.py`), because the requirement
 * taxonomy is still being derived from the gold set. So a name here is either
 * attested somewhere in the repository or it is a placeholder:
 *
 * * `annual_turnover_avg`, and the contract fields `value_usd`,
 *   `completed_year`, `successfully_completed`, are the names the engine's own
 *   end-to-end test uses for a real Section III criterion.
 * * `contracts`, `experts`, `certificates` are the collection names in
 *   `Portfolio`'s docstring.
 * * everything marked `provisional` below is **not** attested. It is a working
 *   name pending docs/OPEN-QUESTIONS.md Q7, chosen so the form can be
 *   built, and it must be reconciled with what extraction actually emits
 *   before any accuracy claim is made.
 *
 * No threshold, percentage or required-field list appears here. A tender says
 * what it requires; this form only records what the vendor has.
 */

/** How one input renders, and how its value is read back. */
export type FieldKind = 'number' | 'year' | 'text' | 'boolean';

export interface ProfileField {
  /** The key written into `scalars`, or into a record of a collection. */
  key: string;
  labelKey: TKey;
  kind: FieldKind;
  /** True when the name is a placeholder — see the module docstring. */
  provisional?: boolean;
}

export interface CollectionSpec {
  /** The key written into `collections`. */
  entity: string;
  titleKey: TKey;
  hintKey: TKey;
  addKey: TKey;
  fields: ProfileField[];
}

/** Single declared values. */
export const SCALAR_FIELDS: ProfileField[] = [
  { key: 'annual_turnover_avg', labelKey: 'profile.field.turnover', kind: 'number' },
  {
    key: 'liquid_assets',
    labelKey: 'profile.field.liquidAssets',
    kind: 'number',
    provisional: true,
  },
];

/** Record sets a count or an `exists` runs over. */
export const COLLECTIONS: CollectionSpec[] = [
  {
    entity: 'contracts',
    titleKey: 'profile.contracts.title',
    hintKey: 'profile.contracts.hint',
    addKey: 'profile.contracts.add',
    fields: [
      { key: 'description', labelKey: 'profile.field.description', kind: 'text' },
      { key: 'value_usd', labelKey: 'profile.field.valueUsd', kind: 'number' },
      { key: 'completed_year', labelKey: 'profile.field.completedYear', kind: 'year' },
      {
        key: 'successfully_completed',
        labelKey: 'profile.field.successfullyCompleted',
        kind: 'boolean',
      },
    ],
  },
  {
    entity: 'experts',
    titleKey: 'profile.experts.title',
    hintKey: 'profile.experts.hint',
    addKey: 'profile.experts.add',
    fields: [
      { key: 'name', labelKey: 'profile.field.expertName', kind: 'text', provisional: true },
      { key: 'role', labelKey: 'profile.field.expertRole', kind: 'text', provisional: true },
      {
        key: 'years_experience',
        labelKey: 'profile.field.yearsExperience',
        kind: 'number',
        provisional: true,
      },
    ],
  },
  {
    entity: 'certificates',
    titleKey: 'profile.certificates.title',
    hintKey: 'profile.certificates.hint',
    addKey: 'profile.certificates.add',
    fields: [
      {
        key: 'name',
        labelKey: 'profile.field.certificateName',
        kind: 'text',
        provisional: true,
      },
      {
        key: 'issued_year',
        labelKey: 'profile.field.issuedYear',
        kind: 'year',
        provisional: true,
      },
    ],
  },
];

/* -------------------------------------------------------------------------
   Naming things the assessment refers to
   ------------------------------------------------------------------------- */

/**
 * The label for a scalar key, or the key itself when it is not one this form
 * collects.
 *
 * The fallback is the point. A tender can require something the profile form
 * has no input for — the taxonomy is open — and showing the raw key is a
 * truthful "we do not have a field for this yet", whereas hiding the row would
 * make the requirement disappear from the explanation.
 */
export function scalarLabel(key: string, t: (key: TKey) => string): string {
  const field = SCALAR_FIELDS.find((candidate) => candidate.key === key);
  return field ? t(field.labelKey) : key;
}

export function entityLabel(entity: string, t: (key: TKey) => string): string {
  const spec = COLLECTIONS.find((candidate) => candidate.entity === entity);
  return spec ? t(spec.titleKey) : entity;
}

export function recordFieldLabel(
  entity: string,
  field: string,
  t: (key: TKey) => string,
): string {
  const spec = COLLECTIONS.find((candidate) => candidate.entity === entity);
  const match = spec?.fields.find((candidate) => candidate.key === field);
  return match ? t(match.labelKey) : field;
}

/** A record being edited: every value is a string until it is submitted. */
export type DraftRecord = Record<string, string>;

export interface ProfileDraft {
  name: string;
  country: string;
  scalars: DraftRecord;
  collections: Record<string, DraftRecord[]>;
}

export function emptyDraft(): ProfileDraft {
  return {
    name: '',
    country: '',
    scalars: {},
    // Every collection starts *absent* rather than as an empty list. The two
    // are different answers: an empty list declares "I have none of these",
    // which the engine reads as a genuine failure, while an absent key leaves
    // the question open. A form the vendor has not touched must not accuse
    // them of having no experience.
    collections: {},
  };
}

/**
 * Turn the draft into the API payload.
 *
 * Blank fields are left out rather than sent as `""`. The backend drops them
 * anyway — an empty string is a declared value that can never satisfy a
 * threshold — but doing it here keeps the request honest about what the vendor
 * actually filled in.
 */
export function draftToPayload(draft: ProfileDraft): VendorProfileInput {
  const scalars: VendorProfileInput['scalars'] = {};
  for (const field of SCALAR_FIELDS) {
    const value = coerce(draft.scalars[field.key], field.kind);
    if (value !== undefined) scalars[field.key] = value;
  }

  const collections: VendorProfileInput['collections'] = {};
  for (const spec of COLLECTIONS) {
    const rows = draft.collections[spec.entity];
    if (rows === undefined) continue;
    collections[spec.entity] = rows
      .map((row) => {
        const record: Record<string, string | number | boolean> = {};
        for (const field of spec.fields) {
          const value = coerce(row[field.key], field.kind);
          if (value !== undefined) record[field.key] = value;
        }
        return record;
      })
      .filter((record) => Object.keys(record).length > 0);
  }

  return { name: draft.name.trim(), country: draft.country.trim(), scalars, collections };
}

/**
 * Read one form value in the type the engine compares with.
 *
 * A number left as text would still compare — `expressions._number` parses
 * `"12,000,000"` — but a year stored as a string would not, because ordering
 * comparisons only run on numbers and dates. Sending the right type is what
 * keeps `completed_year >= 2021` from evaluating to unknown.
 */
function coerce(raw: string | undefined, kind: FieldKind): string | number | boolean | undefined {
  if (raw === undefined) return undefined;
  const value = raw.trim();
  if (!value) return undefined;

  if (kind === 'boolean') {
    // Only an explicit "yes" or "no" is a declaration. Anything else means the
    // vendor has not answered, and unknown is the correct verdict.
    if (value === 'true') return true;
    if (value === 'false') return false;
    return undefined;
  }

  if (kind === 'number' || kind === 'year') {
    const parsed = Number(value.replace(/[\s,]/g, ''));
    return Number.isFinite(parsed) ? parsed : undefined;
  }

  return value;
}

/** Rebuild an editable draft from a stored profile. */
export function payloadToDraft(profile: {
  name: string;
  country: string;
  scalars: Record<string, unknown>;
  collections: Record<string, Record<string, unknown>[]>;
}): ProfileDraft {
  const scalars: DraftRecord = {};
  for (const [key, value] of Object.entries(profile.scalars ?? {})) {
    scalars[key] = String(value);
  }

  const collections: Record<string, DraftRecord[]> = {};
  for (const [entity, records] of Object.entries(profile.collections ?? {})) {
    collections[entity] = records.map((record) => {
      const row: DraftRecord = {};
      for (const [key, value] of Object.entries(record)) row[key] = String(value);
      return row;
    });
  }

  return { name: profile.name, country: profile.country, scalars, collections };
}

/* -------------------------------------------------------------------------
   Which profile this browser belongs to
   ------------------------------------------------------------------------- */


/**
 * There is no sign-in yet (multi-tenancy is deferred), so the profile a
 * browser has created is remembered locally, the same way the theme and the
 * language are. This is a placeholder for accounts, not a substitute: anyone
 * who knows the id can read the profile, which is recorded as a known limit
 * rather than hidden behind a token that would only look like security.
 */
export function storedProfileId(): number | null {
  try {
    const raw = localStorage.getItem(PROFILE_KEY);
    if (!raw) return null;
    const parsed = Number(raw);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null; /* private mode — the profile simply will not be remembered */
  }
}

export function rememberProfileId(id: number): void {
  try {
    localStorage.setItem(PROFILE_KEY, String(id));
  } catch {
    /* nothing to do — the id will have to be re-created next visit */
  }
}
