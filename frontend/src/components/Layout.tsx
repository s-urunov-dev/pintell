import { type ReactNode, useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { useVendorAuth } from '../auth/VendorAuth';
import ChatWidget from './ChatWidget';
import CitationViewer from './CitationViewer';
import { CitationProvider, useCitation } from './CitationDock';
import { useI18n } from '../i18n';
import LanguageSwitcher from './LanguageSwitcher';
import { THEME_KEY } from '../lib/storage';
import BrandMark from './BrandMark';

type Theme = 'light' | 'dark';


function initialTheme(): Theme {
  const stored = localStorage.getItem(THEME_KEY);
  if (stored === 'light' || stored === 'dark') return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

/**
 * The shell, and the provider the citation pane needs to live beside it.
 *
 * Split in two so the shell can *read* the open citation: a provider cannot
 * consume its own context, and putting the pane inside the page instead is
 * what made it an overlay in the first place.
 */
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <CitationProvider>
      <Shell>{children}</Shell>
    </CitationProvider>
  );
}

function Shell({ children }: { children: ReactNode }) {
  const { result: citation, close: closeCitation } = useCitation();
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const location = useLocation();
  const navigate = useNavigate();
  const { t } = useI18n();
  const { email, signOut } = useVendorAuth();
  const signedInAs = email ? t('auth.signedInAs', { email }) : '';

  // The chat is the one route that is not a document. Its thread scrolls
  // inside itself and its composer has to stay under the reader's hands, so
  // the shell is pinned to the viewport there and the page below it never
  // scrolls. The footer goes with it: a strip of provenance that can only be
  // reached by scrolling a page that must not scroll is a strip nobody reads,
  // and leaving it in was what gave the chat a second scrollbar. It is still
  // on every other route, which is where the reader meets it.
  const pinned = location.pathname.startsWith('/chat');

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  // Two keys rather than one interpolated sentence: "light"/"dark" is an
  // adjective that inflects with its noun in Uzbek and Russian.
  const themeLabel = t(theme === 'dark' ? 'layout.switchToLight' : 'layout.switchToDark');

  return (
    <div className={pinned ? 'app-shell app-shell-pinned' : 'app-shell'}>
      <header className="app-header">
        <div className="container header-inner">
          <Link to="/" className="brand" aria-label={t('layout.homeAria')}>
            <span className="brand-mark" aria-hidden="true">
              <BrandMark size="100%" />
            </span>
            <span className="brand-text">
              <strong>Pintell</strong>
              <span className="brand-sub">{t('brand.tagline')}</span>
            </span>
          </Link>

          {/* Every destination stays in place and the current one is marked,
              rather than being hidden while you are on it. A nav that drops
              its own entry changes width and order as you move through the
              site, so the item you want is never twice in the same place —
              and it leaves the reader without the one thing a nav is for:
              saying where they are.

              Companies and Contract Awards are deliberately not here. Both
              are reached from the notice that gives them their meaning — the
              competitor block at the foot of a tender — and a top-level link
              invites reading a supplier list as the product's subject, which
              it is not. */}
          <nav className="header-actions">
            <NavItem to="/" active={location.pathname === '/'}>
              {t('layout.allTenders')}
            </NavItem>
            <NavItem to="/search" active={location.pathname.startsWith('/search')}>
              {t('layout.search')}
            </NavItem>
            <NavItem to="/chat" active={location.pathname.startsWith('/chat')}>
              {t('layout.chat')}
            </NavItem>
            <NavItem to="/experts" active={location.pathname.startsWith('/experts')}>
              {t('layout.experts')}
            </NavItem>
            {/* Signed in: the profile is theirs to edit and the way out is
                beside it. Signed out: one door, and it leads to both forms. */}
            {email ? (
              <>
                <NavItem
                  to="/profile"
                  active={location.pathname === '/profile'}
                  title={signedInAs}
                >
                  {t('layout.profile')}
                </NavItem>
                <button
                  type="button"
                  className="btn btn-ghost"
                  onClick={() => {
                    void signOut();
                    navigate('/');
                  }}
                >
                  {t('auth.signOut')}
                </button>
              </>
            ) : (
              <NavItem to="/sign-in" active={location.pathname === '/sign-in'}>
                {t('auth.signInTitle')}
              </NavItem>
            )}
            <LanguageSwitcher />
            <button
              type="button"
              className="btn btn-icon"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label={themeLabel}
              title={themeLabel}
            >
              {theme === 'dark' ? '☀' : '☾'}
            </button>
          </nav>
        </div>
      </header>

      <div className={citation ? 'app-split app-split-open' : 'app-split'}>
      <main className="app-main">
        <div className="container">{children}</div>
      </main>

      {/* The source, in its own column rather than on top of the page.
          Rendered here — beside `main`, inside neither the header nor the
          footer — so opening it makes the reading column narrower instead of
          covering it, and the two scroll independently. */}
      {citation && <CitationViewer result={citation} onClose={closeCitation} />}
      </div>

      {!pinned && (
        <footer className="app-footer">
          <div className="container footer-inner">
            <p>
              {t('layout.dataSource')}{' '}
              <a
                href="https://search.worldbank.org/api/v2/procnotices"
                target="_blank"
                rel="noopener noreferrer"
              >
                {t('layout.dataSourceName')}
              </a>{' '}
              (
              <a
                href="https://creativecommons.org/licenses/by/4.0/"
                target="_blank"
                rel="noopener noreferrer"
              >
                CC BY 4.0
              </a>
              )
            </p>
            <p className="muted">{t('layout.disclaimer')}</p>
          </div>
        </footer>
      )}

      {/* Mounted once, app-wide, and scoped by the route it sits on: on a
          tender the widget answers about that tender, elsewhere about the
          whole archive. Outside `main` so it floats over the page rather than
          taking a place in the reading order. */}
      {/* Not on the chat's own page: a floating button that opens a small
          copy of the page you are already reading is a second control for the
          same thing, and the one it opens is worse. */}
      {/* Hidden while a source is open, for the same reason the pane is a
          column and not a layer: it would float over the document the reader
          asked to see. */}
      {!location.pathname.startsWith('/chat') && !citation && (
        <ChatWidget noticeId={noticeIdOf(location.pathname)} />
      )}
    </div>
  );
}

/**
 * The tender a route is about, or nothing.
 *
 * Read from the path rather than passed down, because the widget is mounted in
 * the shell and the shell has no props from the page. `/tenders/:id` and
 * `/tenders/:id/compliance` are both about one notice; every other route is
 * about the archive.
 */
function noticeIdOf(pathname: string): string | undefined {
  const match = pathname.match(/^\/tenders\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : undefined;
}

/**
 * One nav destination, marked when it is the page you are on.
 *
 * `aria-current` rather than colour alone: a screen reader gets the same fact
 * the styling gives everyone else. It stays a link on the active page —
 * pressing it is harmless, and disabling it would take a focus stop out of the
 * tab order for no gain.
 */
function NavItem({
  to,
  active,
  title,
  children,
}: {
  to: string;
  active: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <Link
      to={to}
      className={`btn btn-ghost${active ? ' is-active' : ''}`}
      aria-current={active ? 'page' : undefined}
      title={title}
    >
      {children}
    </Link>
  );
}
