import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import * as api from '../api/client';
import type { AdminUser } from '../api/types';

interface AuthState {
  user: AdminUser | null;
  /** True until the initial session probe finishes. */
  initialising: boolean;
  signIn: (username: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null);
  const [initialising, setInitialising] = useState(true);

  // The session lives in an HttpOnly cookie, so "am I signed in?" can only be
  // answered by the server.
  useEffect(() => {
    const controller = new AbortController();
    let active = true;

    api
      .fetchMe(controller.signal)
      .then(({ user: current }) => {
        if (active) setUser(current);
      })
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setInitialising(false);
      });

    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    const { user: current } = await api.login(username, password);
    setUser(current);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // Drop the local session even if the request failed — the operator
      // asked to leave.
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, initialising, signIn, signOut }),
    [user, initialising, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return context;
}
