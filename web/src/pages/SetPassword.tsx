/**
 * Set a password: from the link in an email (?t=TOKEN — a new account, a reset, or a
 * reset an administrator asked for), or by giving the current one.
 */

import { useState } from 'preact/hooks';
import { api } from '../api';
import { Link } from '../router';
import { Button, Field, Notice, UserError, useSubmit } from '../ui';
import { AuthCard, query } from './AuthCard';

export function SetPassword() {
  const token = query('t');
  const required = query('required') === '1';
  const [email, setEmail] = useState(query('email'));
  const [current, setCurrent] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [done, setDone] = useState(false);

  const { busy, error, onSubmit } = useSubmit(async () => {
    if (password !== confirm) throw new UserError("The two passwords don't match.");
    const body = token
      ? { token, new_password: password }
      : { email, current_password: current, new_password: password };
    await api('/password/set', 'POST', body);
    setDone(true);
  });

  if (done) {
    return (
      <AuthCard title="Password set">
        <Notice tone="success">
          Your password has been set.{token && ' If this is a new account, your API key is on its way by email.'}
        </Notice>
        <p><Link href="/login" class="btn btn--primary">Log in</Link></p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={token ? 'Set your password' : 'Change your password'} onSubmit={onSubmit}>
      {required && <Notice>You need to choose a new password before you can log in.</Notice>}
      {error && <Notice tone="error">{error}</Notice>}
      {!token && (
        <>
          <Field label="Email" type="email" value={email} onValue={setEmail} autoComplete="username" required />
          <Field label="Current password" type="password" value={current} onValue={setCurrent} autoComplete="current-password" required />
        </>
      )}
      <Field label="New password" type="password" value={password} onValue={setPassword} autoComplete="new-password"
             hint="At least 8 characters." required />
      <Field label="Confirm new password" type="password" value={confirm} onValue={setConfirm} autoComplete="new-password" required />
      <Button type="submit" variant="primary" busy={busy}>Set password</Button>
      <p class="muted small"><Link href="/login">Back to log in</Link></p>
    </AuthCard>
  );
}
