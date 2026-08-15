import { ApiError } from '../api/client';
import en from '../i18n/messages.en';
import type { MessageKey, MessageParams, TKey } from '../i18n';

type Translate = (key: TKey, params?: MessageParams) => string;

const KNOWN_KEYS = new Set(Object.keys(en));

/**
 * Turn a rejection into a sentence in the current language.
 *
 * Field-level validation errors win when present — "pages: must be at most
 * 200" is actionable in a way that "the request was rejected" is not. After
 * that: a translated string for the error's code, then the backend's own
 * message (already localised via `Accept-Language`), then a generic fallback.
 */
export function errorMessage(error: unknown, t: Translate): string {
  if (!(error instanceof ApiError)) return t('error.unknown');

  const fields = Object.entries(error.details);
  if (fields.length > 0) {
    return fields
      .map(([field, errors]) => `${field}: ${errors.map((e) => e.message).join(', ')}`)
      .join(' · ');
  }

  const key = `error.${error.code}` as MessageKey;
  if (KNOWN_KEYS.has(key)) return t(key, { status: error.status });
  if (error.serverMessage) return error.serverMessage;
  return t('error.http', { status: error.status });
}
