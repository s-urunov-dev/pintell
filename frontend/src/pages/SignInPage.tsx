import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';

import { ApiError } from '../api/client';
import { useVendorAuth } from '../auth/VendorAuth';
import { useI18n } from '../i18n';
import { errorMessage } from '../lib/errors';

/**
 * One page for both signing in and opening an account.
 *
 * Two pages would mean a vendor who guessed wrong has to find the other one,
 * and the two forms differ by two fields. The mode is a toggle, and the
 * heading says which one is active rather than relying on the button label.
 *
 * A vendor arrives here from somewhere — usually the eligibility check on a
 * tender they were reading. `from` carries that back, so signing in returns
 * them to the tender rather than to the home page.
 */
export default function SignInPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const location = useLocation();
  const { signIn, register } = useVendorAuth();

  const from = (location.state as { from?: string } | null)?.from ?? '/profile';

  const [mode, setMode] = useState<'in' | 'up'>('in');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [country, setCountry] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const registering = mode === 'up';

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (registering) {
        await register({ email, password, name, country });
      } else {
        await signIn(email, password);
      }
      navigate(from, { replace: true });
    } catch (rejection) {
      // The server's own sentence when it has one: "an account with this email
      // already exists" and "this password is too common" are both things the
      // vendor can act on, and a generic "the request was invalid" is not.
      setError(
        rejection instanceof ApiError && rejection.serverMessage
          ? rejection.serverMessage
          : errorMessage(rejection, t),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="auth-page">
      <section className="card auth-card">
        <h1>{t(registering ? 'auth.registerTitle' : 'auth.signInTitle')}</h1>
        <p className="muted">{t(registering ? 'auth.registerBody' : 'auth.signInBody')}</p>

        <form onSubmit={submit} className="auth-form">
          {registering && (
            <>
              <div className="field">
                <label htmlFor="auth-name">{t('auth.company')}</label>
                <input
                  id="auth-name"
                  type="text"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  autoComplete="organization"
                  required
                />
              </div>
              <div className="field">
                <label htmlFor="auth-country">{t('auth.country')}</label>
                <input
                  id="auth-country"
                  type="text"
                  value={country}
                  onChange={(event) => setCountry(event.target.value)}
                  autoComplete="country-name"
                />
              </div>
            </>
          )}

          <div className="field">
            <label htmlFor="auth-email">{t('auth.email')}</label>
            <input
              id="auth-email"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              required
            />
          </div>

          <div className="field">
            <label htmlFor="auth-password">{t('auth.password')}</label>
            <input
              id="auth-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              /* Tells a password manager which of the two this is; getting it
                 wrong makes it offer to save a login it should not. */
              autoComplete={registering ? 'new-password' : 'current-password'}
              required
            />
            {registering && <p className="muted small">{t('auth.passwordHint')}</p>}
          </div>

          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}

          <button type="submit" className="btn btn-primary" disabled={busy}>
            {t(
              busy
                ? 'auth.working'
                : registering
                  ? 'auth.registerAction'
                  : 'auth.signInAction',
            )}
          </button>
        </form>

        <p className="muted small auth-switch">
          {t(registering ? 'auth.haveAccount' : 'auth.noAccount')}{' '}
          <button
            type="button"
            className="btn-link"
            onClick={() => {
              setMode(registering ? 'in' : 'up');
              setError(null);
            }}
          >
            {t(registering ? 'auth.signInTitle' : 'auth.registerTitle')}
          </button>
        </p>

        <p className="muted small">
          {t('auth.privacy')} <Link to="/">{t('auth.backHome')}</Link>
        </p>
      </section>
    </article>
  );
}
