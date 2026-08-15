/**
 * Carry an operator's saved theme and language across the rename to Pintell.
 *
 * The same mechanism as the public site's `lib/storage.ts`, and duplicated
 * rather than shared for the same reason the two front ends duplicate their
 * error helpers: they are separate deployables with separate builds, and a
 * shared module between them would be a package to publish.
 *
 * Renaming a storage key does not move what is stored under it — to the
 * browser the new key simply has no value — so the old ones are copied once,
 * on first load, and then removed.
 *
 * Delete this module when no browser can plausibly still hold the old keys.
 */

const LEGACY_PREFIX = 'tenderscope-console-';
const PREFIX = 'pintell-console-';

export const THEME_KEY = `${PREFIX}theme`;
export const LANG_KEY = `${PREFIX}lang`;

/**
 * Move any value still stored under the old prefix onto the new one.
 *
 * Never overwrites: a value already under the new key is this browser's
 * current state. Wrapped, because `localStorage` throws rather than returning
 * null where storage is disabled, and a saved theme is not worth a blank page.
 */
export function migrateLegacyStorage(): void {
  try {
    const stale: string[] = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key && key.startsWith(LEGACY_PREFIX)) stale.push(key);
    }

    for (const key of stale) {
      const renamed = `${PREFIX}${key.slice(LEGACY_PREFIX.length)}`;
      const value = localStorage.getItem(key);
      if (value !== null && localStorage.getItem(renamed) === null) {
        localStorage.setItem(renamed, value);
      }
      localStorage.removeItem(key);
    }
  } catch {
    /* Storage unavailable; the console renders with defaults. */
  }
}
