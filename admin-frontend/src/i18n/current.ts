import { DEFAULT_LANG, type Lang } from './types';

/**
 * The active language, readable outside React.
 *
 * The API client is a plain module — it has no hooks — but every request must
 * carry `Accept-Language` so the backend localises its error messages. The
 * provider mirrors its state here on every change; nothing else writes to it.
 */
let current: Lang = DEFAULT_LANG;

export function getCurrentLang(): Lang {
  return current;
}

export function setCurrentLang(lang: Lang): void {
  current = lang;
}
