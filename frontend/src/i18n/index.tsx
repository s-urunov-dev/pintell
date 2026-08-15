import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import en, { type Catalogue, type TKey } from './messages.en';
import ru from './messages.ru';
import uz from './messages.uz';
import { translateValue, type ValueKind } from './apiValues';
import { setCurrentLang } from './current';
import { LANG_KEY } from '../lib/storage';
import {
  DEFAULT_LANG,
  HTML_LANG,
  INTL_LOCALES,
  UZ_SHORT_MONTHS,
  isLang,
  type Lang,
} from './types';

export { LANGUAGES, LANGUAGE_NAMES, LANGUAGE_SHORT, DEFAULT_LANG, isLang } from './types';
export type { Lang } from './types';
export type { MessageKey, TKey } from './messages.en';

const CATALOGUES: Record<Lang, Catalogue> = { uz, en, ru };


/** Values a message placeholder may take. */
export type MessageParams = Record<string, string | number | null | undefined>;

/**
 * `?lang=` first, so a link can pin a language; then the reader's own stored
 * choice; otherwise Uzbek.
 *
 * The browser's `navigator.language` is deliberately *not* consulted. Uzbek is
 * the product default, and most machines in the target market run an English-
 * or Russian-locale browser — detecting would quietly override the default for
 * exactly the readers it is meant for. The switcher is one click away.
 */
function initialLang(): Lang {
  const fromUrl = new URLSearchParams(window.location.search).get('lang');
  if (isLang(fromUrl)) return fromUrl;
  try {
    const stored = localStorage.getItem(LANG_KEY);
    if (isLang(stored)) return stored;
  } catch {
    /* private mode / storage disabled — fall through to the default */
  }
  return DEFAULT_LANG;
}

interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  /** Translate a UI string, interpolating `{placeholders}`. */
  t: (key: TKey, params?: MessageParams) => string;
  /** Translate an English value that came from the API (country, status, …). */
  tv: (kind: ValueKind, value: string | null | undefined) => string;
  formatDate: (value: string | null | undefined) => string;
  formatDateTime: (value: string | null | undefined) => string;
  /**
   * An instant, rendered as wall-clock time in one zone.
   *
   * Distinct from `formatDate`, which reads everything in UTC because the
   * fields it serves are dates rather than moments. A deadline *is* a moment,
   * and the whole question a reader has is "what time is that where I am" —
   * so this one takes the zone, defaulting to the reader's own.
   */
  formatMoment: (value: string | null | undefined, timeZone?: string) => string;
  /**
   * The calendar date an instant falls on, in the reader's own zone.
   *
   * The third date formatter, and the distinction it draws is the one the
   * deadline display got wrong: `formatDate` reads a date-only field in UTC,
   * which is right for "published on the 8th"; this reads a *moment* and asks
   * which of the reader's days it lands on. A tender closing at 00:30 in
   * Tashkent closes on the 15th for a reader in Tashkent and on the 14th for a
   * reader in Berlin, and both of them are looking at the same instant.
   */
  formatDay: (value: string | null | undefined) => string;
  /** The reader's IANA zone, as their browser reports it. */
  viewerTimeZone: string;
  formatNumber: (value: number | null | undefined) => string;
  formatPercent: (fraction: number) => string;
  formatMoney: (amount: number, currency: string) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

const EM_DASH = '—';

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const detected = initialLang();
    // Publish before the first render so the very first request already
    // carries the right `Accept-Language`.
    setCurrentLang(detected);
    return detected;
  });

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    setCurrentLang(next);
    try {
      localStorage.setItem(LANG_KEY, next);
    } catch {
      /* nothing to do — the choice simply will not persist */
    }
  }, []);

  const value = useMemo<I18nValue>(() => {
    const catalogue = CATALOGUES[lang];
    const locales = INTL_LOCALES[lang];
    const plurals = new Intl.PluralRules(locales);
    const numbers = new Intl.NumberFormat(locales);
    const percents = new Intl.NumberFormat(locales, {
      style: 'percent',
      maximumFractionDigits: 0,
    });
    const dates = new Intl.DateTimeFormat(locales, {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      timeZone: 'UTC',
    });

    // Uzbek is spelled out by hand because browsers ship no month names for
    // it; see `UZ_SHORT_MONTHS`. Everything is read in UTC, matching the
    // formatter's `timeZone` above.
    const renderDate = (date: Date): string =>
      lang === 'uz'
        ? `${String(date.getUTCDate()).padStart(2, '0')} ${
            UZ_SHORT_MONTHS[date.getUTCMonth()]
          } ${date.getUTCFullYear()}`
        : dates.format(date);

    const formatNumber = (input: number | null | undefined) =>
      input === null || input === undefined || Number.isNaN(input)
        ? EM_DASH
        : numbers.format(input);

    /**
     * Wall-clock time in `zone`, in the reader's language.
     *
     * Built from `formatToParts` rather than a plain `format` because Uzbek
     * has no month names in any browser's ICU data (`UZ_SHORT_MONTHS` is the
     * hand-written table). Asking for the parts lets the month be substituted
     * while the zone conversion — the part that must be right — is still done
     * by `Intl`, which knows the offsets and their historical changes.
     */
    const renderMoment = (date: Date, zone: string): string => {
      const options: Intl.DateTimeFormatOptions = {
        timeZone: zone,
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      };
      if (lang !== 'uz') {
        return new Intl.DateTimeFormat(locales, options).format(date);
      }
      const parts = new Intl.DateTimeFormat('en-GB', {
        ...options,
        month: 'numeric',
      }).formatToParts(date);
      const get = (type: Intl.DateTimeFormatPartTypes) =>
        parts.find((part) => part.type === type)?.value ?? '';
      const month = UZ_SHORT_MONTHS[Number(get('month')) - 1] ?? get('month');
      return `${get('day')} ${month} ${get('year')}, ${get('hour')}:${get('minute')}`;
    };

    /**
     * The date part of `renderMoment`, in the same language and the same zone.
     *
     * Written as its own formatter rather than as a slice of the moment string
     * because the two differ by more than a substring in Russian, where the
     * time is joined with a comma and a space that a slice would have to know
     * about.
     */
    const renderDay = (date: Date, zone: string): string => {
      const options: Intl.DateTimeFormatOptions = {
        timeZone: zone,
        day: '2-digit',
        month: 'short',
        year: 'numeric',
      };
      if (lang !== 'uz') {
        return new Intl.DateTimeFormat(locales, options).format(date);
      }
      const parts = new Intl.DateTimeFormat('en-GB', {
        ...options,
        month: 'numeric',
      }).formatToParts(date);
      const get = (type: Intl.DateTimeFormatPartTypes) =>
        parts.find((part) => part.type === type)?.value ?? '';
      const month = UZ_SHORT_MONTHS[Number(get('month')) - 1] ?? get('month');
      return `${get('day')} ${month} ${get('year')}`;
    };

    // Asked once per render of the provider, not per call: the answer cannot
    // change while the tab is open, and it is the fallback for every moment.
    const viewerTimeZone =
      Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

    /**
     * Resolve a plural key: `foo` + `count` looks for `foo_<category>` and
     * falls back to `foo_other`, so a language that does not define `_few`
     * still renders.
     */
    const lookup = (key: TKey, params?: MessageParams): string => {
      const table = catalogue as Record<string, string | undefined>;
      const count = params?.count;
      if (typeof count === 'number') {
        const category = plurals.select(count);
        return (
          table[`${key}_${category}`] ??
          table[`${key}_other`] ??
          table[key] ??
          (en as Record<string, string>)[`${key}_other`] ??
          key
        );
      }
      return table[key] ?? (en as Record<string, string>)[key] ?? key;
    };

    const interpolate = (template: string, params?: MessageParams): string => {
      if (!params) return template;
      return template.replace(/\{(\w+)\}/g, (match, name: string) => {
        const raw = params[name];
        if (raw === null || raw === undefined) return match;
        return typeof raw === 'number' ? numbers.format(raw) : raw;
      });
    };

    const t = (key: TKey, params?: MessageParams) =>
      interpolate(lookup(key, params), params);

    const toDate = (input: string | null | undefined): Date | null => {
      if (!input) return null;
      const parsed = new Date(input);
      return Number.isNaN(parsed.getTime()) ? null : parsed;
    };

    const formatDate = (input: string | null | undefined) => {
      const parsed = toDate(input);
      return parsed ? renderDate(parsed) : EM_DASH;
    };

    return {
      lang,
      setLang,
      t,
      tv: (kind, input) => translateValue(kind, input, lang),
      formatDate,
      formatDateTime: (input) => {
        const parsed = toDate(input);
        if (!parsed) return EM_DASH;
        // The clock half stays ISO/UTC on purpose: these are upstream sync
        // timestamps, and a locale-shifted time would misreport them.
        return `${renderDate(parsed)}, ${parsed.toISOString().slice(11, 16)} UTC`;
      },
      formatDay: (input) => {
        const parsed = toDate(input);
        if (!parsed) return EM_DASH;
        try {
          return renderDay(parsed, viewerTimeZone);
        } catch {
          return renderDay(parsed, 'UTC');
        }
      },
      formatMoment: (input, timeZone) => {
        const parsed = toDate(input);
        if (!parsed) return EM_DASH;
        try {
          return renderMoment(parsed, timeZone || viewerTimeZone);
        } catch {
          // An unknown zone name — upstream data reaching a browser that does
          // not know it. UTC is wrong for the reader but never wrong about
          // the instant, and the caller labels which zone it printed.
          return renderMoment(parsed, 'UTC');
        }
      },
      viewerTimeZone,
      formatNumber,
      formatPercent: (fraction) => percents.format(fraction),
      formatMoney: (amount, currency) => {
        const code = (currency || '').trim().toUpperCase();
        // Upstream sometimes carries a non-ISO code; `Intl` throws on those,
        // so fall back to "CODE 1 234,56".
        if (/^[A-Z]{3}$/.test(code)) {
          try {
            return new Intl.NumberFormat(locales, {
              style: 'currency',
              currency: code,
              maximumFractionDigits: 2,
            }).format(amount);
          } catch {
            /* fall through to the plain form */
          }
        }
        const formatted = new Intl.NumberFormat(locales, {
          maximumFractionDigits: 2,
        }).format(amount);
        return code ? `${code} ${formatted}` : formatted;
      },
    };
  }, [lang, setLang]);

  // Keep the document itself in the chosen language: `lang` drives hyphenation
  // and screen-reader pronunciation, and the title/description are what a
  // shared link previews as.
  useEffect(() => {
    document.documentElement.lang = HTML_LANG[lang];
    document.title = value.t('meta.title');
    const description = document.querySelector('meta[name="description"]');
    if (description) description.setAttribute('content', value.t('meta.description'));
  }, [lang, value]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useI18n must be used inside <I18nProvider>');
  }
  return context;
}
