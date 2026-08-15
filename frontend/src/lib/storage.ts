/**
 * Carry a returning visitor's saved state across the rename to Pintell.
 *
 * The theme, the interface language and — the one that actually costs
 * something — the draft vendor profile all live in `localStorage`, under keys
 * that were prefixed with the product's old name. Renaming a storage key does
 * not move what is stored under it: to the browser the new key simply has no
 * value, so the site would greet everyone who had used it before with the
 * default theme, the default language, and an empty profile form they had
 * already filled in.
 *
 * So the old keys are copied once, on first load, and then removed. Copy
 * rather than read-through, because a read-through fallback has to stay
 * forever and this has to run exactly once per browser.
 *
 * Delete this module when no browser can plausibly still hold the old keys.
 */

const LEGACY_PREFIX = 'tenderscope-';
const PREFIX = 'pintell-';

/** Storage keys, so the prefix is written once rather than at five call sites. */
export const THEME_KEY = `${PREFIX}theme`;
export const LANG_KEY = `${PREFIX}lang`;
export const PROFILE_KEY = `${PREFIX}vendor-profile`;

/**
 * Move any value still stored under the old prefix onto the new one.
 *
 * Never overwrites: a value already under the new key is this browser's
 * current state and is newer than anything left behind by the old build.
 *
 * Wrapped, because `localStorage` throws rather than returning null in a
 * browser with storage disabled or a quota exhausted — and losing a saved
 * theme is not a reason to fail to render the site.
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
    /* Storage unavailable. The site renders with defaults, which is the same
       outcome a first-time visitor gets. */
  }
}
