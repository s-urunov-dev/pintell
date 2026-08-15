import { useEffect, useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { DJANGO_ADMIN_URL, PUBLIC_SITE_URL } from '../config';
import { useI18n, type MessageKey } from '../i18n';
import LanguageSwitcher from './LanguageSwitcher';
import { THEME_KEY } from '../lib/storage';
import BrandMark from './BrandMark';

const NAV_ITEMS: { to: string; label: MessageKey; end?: boolean; icon: string }[] = [
  { to: '/', label: 'shell.nav.dashboard', end: true, icon: '▦' },
  { to: '/sync-runs', label: 'shell.nav.syncRuns', icon: '⟳' },
  { to: '/backfill', label: 'shell.nav.backfill', icon: '▤' },
  { to: '/compliance', label: 'shell.nav.compliance', icon: '✓' },
  { to: '/explorer', label: 'shell.nav.explorer', icon: '🗂' },
  { to: '/requirements', label: 'shell.nav.requirements', icon: '⚖' },
  { to: '/notices', label: 'shell.nav.notices', icon: '☰' },
  { to: '/index', label: 'shell.nav.index', icon: '◈' },
  { to: '/system', label: 'shell.nav.system', icon: '◍' },
];

type Theme = 'light' | 'dark';

function initialTheme(): Theme {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export default function ConsoleLayout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const { t } = useI18n();
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  const handleSignOut = async () => {
    await signOut();
    navigate('/login', { replace: true });
  };

  return (
    <div className="console-shell">
      <aside className={`console-sidebar ${navOpen ? 'open' : ''}`}>
        <div className="console-brand">
          <span className="brand-mark" aria-hidden="true">
            <BrandMark size="100%" />
          </span>
          <span className="brand-text">
            <strong>Pintell</strong>
            <span className="brand-sub">{t('shell.brandSub')}</span>
          </span>
        </div>

        <nav className="console-nav" aria-label={t('shell.navAria')}>
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `console-nav-link ${isActive ? 'active' : ''}`}
              onClick={() => setNavOpen(false)}
            >
              <span className="console-nav-icon" aria-hidden="true">{item.icon}</span>
              {t(item.label)}
            </NavLink>
          ))}
        </nav>

        <div className="console-sidebar-footer">
          {PUBLIC_SITE_URL && (
            <a
              className="console-nav-link subtle"
              href={PUBLIC_SITE_URL}
              target="_blank"
              rel="noreferrer"
            >
              <span className="console-nav-icon" aria-hidden="true">↗</span>
              {t('shell.publicSite')}
            </a>
          )}
          {/* The Django admin remains the low-level developer tool. */}
          <a
            className="console-nav-link subtle"
            href={DJANGO_ADMIN_URL}
            target="_blank"
            rel="noreferrer"
          >
            <span className="console-nav-icon" aria-hidden="true">⚙</span>
            {t('shell.djangoAdmin')}
          </a>
        </div>
      </aside>

      {navOpen && (
        <button
          type="button"
          className="console-scrim"
          aria-label={t('shell.closeNav')}
          onClick={() => setNavOpen(false)}
        />
      )}

      <div className="console-main">
        <header className="console-topbar">
          <button
            type="button"
            className="btn btn-icon console-nav-toggle"
            onClick={() => setNavOpen((open) => !open)}
            aria-label={t('shell.toggleNav')}
            aria-expanded={navOpen}
          >
            ☰
          </button>

          <div className="console-topbar-actions">
            <LanguageSwitcher />
            <button
              type="button"
              className="btn btn-icon"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              // Two keys, not one interpolated sentence: "light"/"dark" is an
              // adjective that inflects with its noun in Uzbek and Russian.
              aria-label={t(theme === 'dark' ? 'shell.switchToLight' : 'shell.switchToDark')}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
            <span className="console-user" title={user?.email || undefined}>
              <span className="console-avatar" aria-hidden="true">
                {(user?.username ?? '?').slice(0, 1).toUpperCase()}
              </span>
              {user?.username}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={handleSignOut}>
              {t('shell.signOut')}
            </button>
          </div>
        </header>

        <main className="console-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
