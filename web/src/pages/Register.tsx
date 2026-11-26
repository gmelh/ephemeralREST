import { useState } from 'preact/hooks';
import { api } from '../api';
import { config } from '../config';
import { Link } from '../router';
import { Button, Field, Notice, useSubmit } from '../ui';
import { AuthCard } from './AuthCard';

export function Register() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const { busy, error, onSubmit } = useSubmit(async () => {
    const result = await api<{ message: string }>('/register', 'POST', { name, email });
    setSent(result.message);
  });

  if (!config.allow_registration) {
    return (
      <AuthCard title="Registration is closed">
        <p class="muted">New accounts can't be created here. Ask the administrator for access.</p>
        <p class="muted small"><Link href="/login">Back to log in</Link></p>
      </AuthCard>
    );
  }
  if (sent) {
    return (
      <AuthCard title="Check your email">
        <Notice tone="success">{sent}</Notice>
        <p class="muted">The email has a link to confirm your address. After that you'll set a password and get your API key.</p>
        <p class="muted small"><Link href="/login">Back to log in</Link></p>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Create an account" onSubmit={onSubmit}>
      <p class="muted">An account gives you an API key for calling the API from your own apps.</p>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Name" value={name} onValue={setName} autoComplete="name" required />
      <Field label="Email" type="email" value={email} onValue={setEmail} autoComplete="email" required />
      <Button type="submit" variant="primary" busy={busy}>Create account</Button>
      <p class="muted small">Already have one? <Link href="/login">Log in</Link></p>
    </AuthCard>
  );
}
