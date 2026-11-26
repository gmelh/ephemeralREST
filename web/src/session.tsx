/** The logged-in user, available to every screen. */

import { createContext } from 'preact';
import { useContext } from 'preact/hooks';
import type { Session } from './api';

export interface SessionContextValue {
  session: Session;
  /** Log out; `forgetDevice` also makes this browser ask for a 2FA code next time. */
  signOut: (forgetDevice?: boolean) => Promise<void>;
  updateUser: (session: Session) => void;
}

export const SessionContext = createContext<SessionContextValue | null>(null);

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession() used outside a signed-in screen');
  return value;
}
