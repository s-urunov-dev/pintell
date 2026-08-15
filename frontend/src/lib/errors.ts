import { ApiError } from '../api/client';
import en from '../i18n/messages.en';
import type { MessageKey, MessageParams, TKey } from '../i18n';

type Translate = (key: TKey, params?: MessageParams) => string;

const KNOWN_KEYS = new Set(Object.keys(en));

/**
 * Turn a rejection into a sentence in the current language.
 *
 * Order of preference:
 *  1. a translated string for the error's code (`error.not_found`, …);
 *  2. the message the backend sent — it is already localised via
 *     `Accept-Language`, so an unfamiliar code still reads correctly;
 *  3. a generic fallback.
 */
export function errorMessage(error: unknown, t: Translate): string {
  if (error instanceof ApiError) {
    const key = `error.${error.code}` as MessageKey;
    if (KNOWN_KEYS.has(key)) {
      return t(key, { status: error.status });
    }
    if (error.serverMessage) return error.serverMessage;
    return t('error.http', { status: error.status });
  }
  return t('error.unknown');
}
