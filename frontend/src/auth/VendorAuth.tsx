import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { fetchVendorSession, loginVendor, logoutVendor, registerVendor } from '../api/client';
import type { VendorProfile } from '../api/types';

/**
 * Who is signed in, held once for the whole app.
 *
 * The session lives in a cookie the browser manages; this context holds only
 * what the UI needs to render — the email and the profile — and never a
 * credential. Signing out clears it here *and* on the server, so a stale tab
 * cannot keep showing a name after the session has gone.
 *
 * `initialising` is separate from "not signed in". The boot call takes a
 * round-trip, and rendering the signed-out state during it would flash a "sign
 * in" prompt at someone who already is.
 */
interface VendorAuthValue {
  email: string | null;
  profile: VendorProfile | null;
  initialising: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  register: (input: {
    email: string;
    password: string;
    name: string;
    country?: string;
  }) => Promise<void>;
  signOut: () => Promise<void>;
  /** Replace the cached profile after the vendor edits it. */
  setProfile: (profile: VendorProfile) => void;
}

const VendorAuthContext = createContext<VendorAuthValue | null>(null);

export function VendorAuthProvider({ children }: { children: ReactNode }) {
  const [email, setEmail] = useState<string | null>(null);
  const [profile, setProfile] = useState<VendorProfile | null>(null);
  const [initialising, setInitialising] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    fetchVendorSession(controller.signal)
      .then((session) => {
        setEmail(session.user?.email ?? null);
        setProfile(session.profile);
      })
      .catch(() => {
        // A visitor with no session is the ordinary case and the endpoint
        // answers 200 for it, so anything caught here is the API being
        // unreachable. Treat it as signed out: the pages that need an account
        // will say so, and the ones that do not still work.
      })
      .finally(() => {
        if (!controller.signal.aborted) setInitialising(false);
      });
    return () => controller.abort();
  }, []);

  const signIn = useCallback(async (address: string, password: string) => {
    const session = await loginVendor({ email: address, password });
    setEmail(session.user?.email ?? null);
    setProfile(session.profile);
  }, []);

  const register = useCallback(
    async (input: { email: string; password: string; name: string; country?: string }) => {
      const session = await registerVendor(input);
      setEmail(session.user?.email ?? null);
      setProfile(session.profile);
    },
    [],
  );

  const signOut = useCallback(async () => {
    try {
      await logoutVendor();
    } finally {
      // Cleared even if the request failed. The alternative — keeping someone
      // "signed in" locally because the server did not answer — shows a name
      // and a profile to whoever is now at the keyboard.
      setEmail(null);
      setProfile(null);
    }
  }, []);

  const value = useMemo<VendorAuthValue>(
    () => ({ email, profile, initialising, signIn, register, signOut, setProfile }),
    [email, profile, initialising, signIn, register, signOut],
  );

  return <VendorAuthContext.Provider value={value}>{children}</VendorAuthContext.Provider>;
}

export function useVendorAuth(): VendorAuthValue {
  const value = useContext(VendorAuthContext);
  if (!value) throw new Error('useVendorAuth must be used inside VendorAuthProvider');
  return value;
}
