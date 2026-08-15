/** Build-time configuration for the console. */

/**
 * Same-origin by default: this app's nginx (and the Vite dev server) proxy
 * `/api` to Django, which is what keeps the session cookie `SameSite=Lax`.
 * Point VITE_API_BASE_URL at another host only if you also relax the cookie
 * policy on the backend.
 */
export const API_BASE =
  (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '') || '/api';

export const ADMIN_API_BASE = `${API_BASE}/admin`;

/**
 * The semantic index lives under its own versioned prefix rather than under
 * `/api/admin/`, because what it serves is a rebuildable cache and not part of
 * the console's own data. The console reads one staff-only endpoint from it —
 * the index status — which is why this constant exists at all.
 */
export const RAG_API_BASE = `${API_BASE}/v1`;

/** Link back to the public site; empty hides the link. */
export const PUBLIC_SITE_URL = import.meta.env.VITE_PUBLIC_SITE_URL ?? '';

/** Django admin, proxied by this service's nginx. */
export const DJANGO_ADMIN_URL = '/admin/';
