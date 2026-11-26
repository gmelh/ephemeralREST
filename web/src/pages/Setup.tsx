/** First run: create the administrator. Shown only while the install has no accounts. */

import { useState } from 'preact/hooks';
import { api, type Session } from '../api';
import { config } from '../config';
import { Button, CopyText, Field, Notice, UserError, useSubmit } from '../ui';
import { AuthCard } from './AuthCard';

export function Setup({ onDone }: { onDone: (session: Session) => void }) {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [created, setCreated] = useState<{ session: Session; api_key: string } | null>(null);

  const { busy, error, onSubmit } = useSubmit(async () => {
    if (password !== confirm) throw new UserError("The two passwords don't match.");
    setCreated(await api<{ session: Session; api_key: string }>('/auth/setup', 'POST', { name, email, password }));
  });

  if (created) {
    return (
      <AuthCard title="You're set up">
        <p>This is your API key, for calling the API from your own apps. It's shown only now — keep it somewhere safe.
          (You can always make a new one from your account.)</p>
        <CopyText text={created.api_key} />
        <Button variant="primary" onClick={() => onDone(created.session)}>Continue</Button>
      </AuthCard>
    );
  }

  return (
    <AuthCard title={`Welcome to ${config.site_name || 'ephemeralREST'}`} onSubmit={onSubmit}>
      <p class="muted">Create the administrator account. Other people can register, or you can add them, later.</p>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Your name" value={name} onValue={setName} autoComplete="name" required />
      <Field label="Email" type="email" value={email} onValue={setEmail} autoComplete="email" required />
      <Field label="Password" type="password" value={password} onValue={setPassword} autoComplete="new-password"
             hint="At least 8 characters. A few random words works well." required />
      <Field label="Confirm password" type="password" value={confirm} onValue={setConfirm} autoComplete="new-password" required />
      <Button type="submit" variant="primary" busy={busy}>Create account</Button>
    </AuthCard>
  );
}
