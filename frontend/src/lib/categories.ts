import type { MessageKey, MessageParams, TKey } from '../i18n';
import en from '../i18n/messages.en';
import type { TenderCategory } from '../api/types';

type Translate = (key: TKey, params?: MessageParams) => string;

/**
 * The directions, in the order a filter should offer them.
 *
 * A list rather than only a set, because two consumers need different things
 * from the same vocabulary: `categoryLabel` asks "is this one of ours" and a
 * dropdown asks "what are they, in order". Deriving the set from the list
 * keeps a new direction to one edit.
 *
 * `unknown` is deliberately last: it is a real state of the mirror (most of it,
 * in fact) and hiding it from a filter would make the unclassified majority
 * unreachable.
 */
export const CATEGORY_KEYS = [
  'construction',
  'consulting',
  'supply',
  'services',
  'it',
  'other',
  'unknown',
] as const satisfies readonly TenderCategory[];

const CATEGORIES = new Set<string>(CATEGORY_KEYS);

/**
 * Category labels are translated rather than stored, so these take `t`. An
 * unknown value (a category the backend gained before the frontend did) falls
 * through as-is instead of rendering a raw key.
 */
export function categoryLabel(value: string, t: Translate): string {
  return CATEGORIES.has(value) ? t(`category.${value}` as MessageKey) : value;
}

/** Short labels for chips, where horizontal space is tight. */
export function categoryShort(value: string, t: Translate): string {
  return CATEGORIES.has(value) ? t(`categoryShort.${value}` as MessageKey) : value;
}

/**
 * How the category was decided — surfaced so a user can tell an AI-read
 * classification from a keyword match.
 */
export function categoryOrigin(source: string, t: Translate): string {
  const key = `categorySource.${source}` as MessageKey;
  return key in en ? t(key) : t('categorySource.none');
}

const SUBCATEGORIES = new Set<string>([
  'engineering',
  'audit',
  'environment_social',
  'training',
  'research',
  'it_advisory',
  'legal_procurement',
  'management',
  'other',
]);

/**
 * Label for a sub-direction inside Consulting. Falls through unchanged for a
 * value the frontend does not know yet, same as `categoryLabel`.
 */
export function subcategoryLabel(value: string, t: Translate): string {
  return SUBCATEGORIES.has(value) ? t(`subcategory.${value}` as MessageKey) : value;
}

/**
 * Short label for the card chip, or `''` when the sub-direction is not worth
 * the space. `other` and `unknown` both mean "the rules could not tell", which
 * the category tag beside it already conveys.
 */
export function subcategoryShort(value: string, t: Translate): string {
  const key = `subcategoryShort.${value}` as MessageKey;
  return key in en ? t(key) : '';
}

const AUDIENCES = new Set<string>(['firm', 'individual']);

/**
 * Label for who a consulting notice is addressed to. Only the two answered
 * values have labels: an empty audience means the selection method does not
 * say, and naming that would present an open question as a third audience.
 */
export function audienceLabel(value: string, t: Translate): string {
  return AUDIENCES.has(value) ? t(`audience.${value}` as MessageKey) : value;
}
