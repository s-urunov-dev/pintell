/** The three languages the product ships in. Uzbek is the default. */
export const LANGUAGES = ['uz', 'en', 'ru'] as const;

export type Lang = (typeof LANGUAGES)[number];

export const DEFAULT_LANG: Lang = 'uz';

/** Native names for the switcher — a language is always named in itself. */
export const LANGUAGE_NAMES: Record<Lang, string> = {
  uz: "O'zbekcha",
  en: 'English',
  ru: 'Русский',
};

export const LANGUAGE_SHORT: Record<Lang, string> = {
  uz: 'UZ',
  en: 'EN',
  ru: 'RU',
};

/**
 * BCP-47 tags handed to `Intl`, most specific first. The English fallback is
 * deliberate: if a runtime has no Uzbek or Russian data, dates and numbers
 * still render rather than throwing.
 */
export const INTL_LOCALES: Record<Lang, string[]> = {
  uz: ['uz-UZ', 'uz', 'en-GB'],
  en: ['en-GB'],
  ru: ['ru-RU', 'ru', 'en-GB'],
};

/** The `lang` attribute written onto `<html>`. */
export const HTML_LANG: Record<Lang, string> = {
  uz: 'uz',
  en: 'en',
  ru: 'ru',
};

/**
 * Uzbek abbreviated month names.
 *
 * Not a preference — a necessity. Chrome's bundled ICU has no month names for
 * `uz` and falls back to the root locale's placeholders, so
 * `Intl.DateTimeFormat('uz', {month: 'short'})` renders 30 July 2026 as
 * "2026 M07 30". Russian and English come out of `Intl` correctly and are
 * left to it.
 */
export const UZ_SHORT_MONTHS = [
  'yan',
  'fev',
  'mar',
  'apr',
  'may',
  'iyn',
  'iyl',
  'avg',
  'sen',
  'okt',
  'noy',
  'dek',
] as const;

export function isLang(value: unknown): value is Lang {
  return typeof value === 'string' && (LANGUAGES as readonly string[]).includes(value);
}
