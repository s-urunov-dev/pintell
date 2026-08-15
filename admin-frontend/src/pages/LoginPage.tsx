import { type FormEvent, useState } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '../auth/AuthContext';
import { PUBLIC_SITE_URL } from '../config';
import BrandMark from '../components/BrandMark';
import LanguageSwitcher from '../components/LanguageSwitcher';
import { useDocumentTitle, useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

export default function LoginPage() {
  const { user, initialising, signIn } = useAuth();
  const { t } = useI18n();
  useDocumentTitle('title.signIn');
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // The rejection, not a message: translated at render so a language switch
  // re-translates an error already on screen.
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (!initialising && user) {
    const from = (location.state as { from?: string } | null)?.from;
    return <Navigate to={from && from !== '/login' ? from : '/'} replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username, password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== '/login' ? from : '/', { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={handleSubmit}>
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true">
            <BrandMark size="100%" />
          </span>
          <div>
            <h1>{t('login.heading')}</h1>
            <p className="muted small">{t('login.subtitle')}</p>
          </div>
          {/* The switcher belongs here too: an operator who cannot read the
              default language must be able to change it before signing in. */}
          <LanguageSwitcher />
        </div>

        {error != null && (
          <div className="banner banner-critical" role="alert">
            {errorMessage(error, t)}
          </div>
        )}

        <div className="field">
          <label htmlFor="login-username">{t('login.username')}</label>
          <input
            id="login-username"
            name="username"
            autoComplete="username"
            autoFocus
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>

        <div className="field">
          <label htmlFor="login-password">{t('login.password')}</label>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>

        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? t('login.submitting') : t('login.submit')}
        </button>

        <p className="muted small login-footnote">
          {t('login.footnote')}
          {PUBLIC_SITE_URL && (
            <>
              {' '}
              <a href={PUBLIC_SITE_URL}>{t('shell.publicSite')}</a>
            </>
          )}
        </p>
      </form>
    </div>
  );
}
