/**
 * Top level: asks the server whether this install needs setting up and who is
 * logged in, then shows the setup screen, a signed-out page, or the app.
 */

import type { JSX } from 'preact';
import { useCallback, useEffect, useState } from 'preact/hooks';
import { api, onSessionEnded, setCsrfToken, type AuthStatus, type Session } from './api';
import { setConfig } from './config';
import { ForgotPassword } from './pages/ForgotPassword';
import { Login } from './pages/Login';
import { Register } from './pages/Register';
import { SetPassword } from './pages/SetPassword';
import { Setup } from './pages/Setup';
import { Shell } from './pages/Shell';
import { Verify } from './pages/Verify';
import { navigate, usePath } from './router';
import { SessionContext } from './session';
import { describe, Notice } from './ui';

type State =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'setup' }
  | { kind: 'signed-out' }
  | { kind: 'app'; session: Session };

/** Pages that work whether or not someone is signed in (links in emails land here). */
const PUBLIC: Record<string, () => JSX.Element> = {
  '/verify': () => <Verify />,
  '/set-password': () => <SetPassword />,
  '/forgot-password': () => <ForgotPassword />,
  '/register': () => <Register />,
};

export function App() {
  const [state, setState] = useState<State>({ kind: 'loading' });
  const path = usePath();

  const signedIn = useCallback((session: Session) => {
    setCsrfToken(session.csrf_token);
    setState({ kind: 'app', session });
  }, []);

  const signedOut = useCallback(() => {
    setCsrfToken(null);
    setState({ kind: 'signed-out' });
  }, []);

  useEffect(() => {
    onSessionEnded(signedOut);
    api<AuthStatus>('/auth/status')
      .then((status) => {
        setConfig(status.config);
        if (status.setup_required) setState({ kind: 'setup' });
        else if (status.session) signedIn(status.session);
        else setState({ kind: 'signed-out' });
      })
      .catch((error: unknown) => setState({ kind: 'error', message: describe(error) }));
  }, [signedIn, signedOut]);

  if (state.kind === 'loading') return null;
  if (state.kind === 'error') {
    return (
      <main class="auth">
        <Notice tone="error">{state.message}</Notice>
      </main>
    );
  }
  if (state.kind === 'setup') {
    return <Setup onDone={(session) => { navigate('/', { replace: true }); signedIn(session); }} />;
  }

  const publicPage = PUBLIC[path];
  if (publicPage) return publicPage();

  if (state.kind === 'signed-out') {
    return <Login onDone={(session) => {
      if (path === '/login') navigate('/', { replace: true });
      signedIn(session);
    }} />;
  }

  const signOut = async (forgetDevice = false) => {
    await api('/auth/logout', 'POST', { forget_device: forgetDevice }).catch(() => undefined);
    navigate('/', { replace: true });
    signedOut();
  };
  return (
    <SessionContext.Provider value={{ session: state.session, signOut, updateUser: signedIn }}>
      <Shell />
    </SessionContext.Provider>
  );
}
