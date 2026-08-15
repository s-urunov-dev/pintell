import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import en, { type Catalogue, type MessageKey, type TKey } from './messages.en';
import ru from './messages.ru';
import uz from './messages.uz';
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


export type MessageParams = Record<string, string | number | null | undefined>;

/**
 * `?lang=` first, so a link can pin a language; then the operator's own stored
 * choice; otherwise Uzbek.
 *
 * The browser's `navigator.language` is deliberately *not* consulted — see the
 * same function in the public app for why.
 */
function initialLang(): Lang {
  const fromUrl = new URLSearchParams(window.location.search).get('lang');
  if (isLang(fromUrl)) return fromUrl;
  try {
    const stored = localStorage.getItem(LANG_KEY);
    if (isLang(stored)) return stored;
  } catch {
    /* storage disabled — fall through to the default */
  }
  return DEFAULT_LANG;
}

interface I18nValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: TKey, params?: MessageParams) => string;
  /** Localise a job status (`running`, `failed`, …); unknown values pass through. */
  tStatus: (status: string) => string;
  formatDate: (value: string | null | undefined) => string;
  formatDateTime: (value: string | null | undefined) => string;
  formatNumber: (value: number | null | undefined) => string;
}

const I18nContext = createContext<I18nValue | null>(null);

const EM_DASH = '—';

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    const detected = initialLang();
    // Published before the first render so the first request already carries
    // the right `Accept-Language`.
    setCurrentLang(detected);
    return detected;
  });

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    setCurrentLang(next);
    try {
      localStorage.setItem(LANG_KEY, next);
    } catch {
      /* the choice simply will not persist */
    }
  }, []);

  const value = useMemo<I18nValue>(() => {
    const catalogue = CATALOGUES[lang];
    const locales = INTL_LOCALES[lang];
    const plurals = new Intl.PluralRules(locales);
    const numbers = new Intl.NumberFormat(locales);
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

    const t = (key: TKey, params?: MessageParams): string => {
      const template = lookup(key, params);
      if (!params) return template;
      return template.replace(/\{(\w+)\}/g, (match, name: string) => {
        const raw = params[name];
        if (raw === null || raw === undefined) return match;
        return typeof raw === 'number' ? numbers.format(raw) : raw;
      });
    };

    const toDate = (input: string | null | undefined): Date | null => {
      if (!input) return null;
      const parsed = new Date(input);
      return Number.isNaN(parsed.getTime()) ? null : parsed;
    };

    return {
      lang,
      setLang,
      t,
      tStatus: (status) => {
        const key = `status.${status}` as MessageKey;
        return key in en ? t(key) : status;
      },
      formatDate: (input) => {
        const parsed = toDate(input);
        return parsed ? renderDate(parsed) : EM_DASH;
      },
      formatDateTime: (input) => {
        const parsed = toDate(input);
        if (!parsed) return EM_DASH;
        // The clock half stays ISO/UTC: these are server-side job timestamps,
        // and a locale-shifted time would misreport when a run happened.
        return `${renderDate(parsed)}, ${parsed.toISOString().slice(11, 16)} UTC`;
      },
      formatNumber: (input) =>
        input === null || input === undefined || Number.isNaN(input)
          ? EM_DASH
          : numbers.format(input),
    };
  }, [lang, setLang]);

  useEffect(() => {
    document.documentElement.lang = HTML_LANG[lang];
  }, [lang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const context = useContext(I18nContext);
  if (!context) {
    throw new Error('useI18n must be used inside <I18nProvider>');
  }
  return context;
}

/** Sets the tab title to "<page> · <console>", re-running on a language switch. */
export function useDocumentTitle(key: MessageKey): void {
  const { t, lang } = useI18n();
  useEffect(() => {
    document.title = `${t(key)} · ${t('title.suffix')}`;
    // `lang` is the real dependency: `t` is rebuilt whenever it changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, lang]);
}
