import { useState } from 'preact/hooks';
import { api } from '../api';
import { Link } from '../router';
import { Button, Field, Notice, useSubmit } from '../ui';
import { AuthCard } from './AuthCard';

export function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState<string | null>(null);
  const { busy, error, onSubmit } = useSubmit(async () => {
    const result = await api<{ message: string }>('/password/forgot', 'POST', { email });
    setSent(result.message);
  });

  if (sent) {
    return (
      <AuthCard title="Check your email">
        <Notice tone="success">{sent}</Notice>
        <p class="muted small"><Link href="/login">Back to log in</Link></p>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="Forgotten your password?" onSubmit={onSubmit}>
      <p class="muted">Enter your email address and we'll send you a link to set a new one.</p>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Email" type="email" value={email} onValue={setEmail} autoComplete="email" required />
      <Button type="submit" variant="primary" busy={busy}>Send link</Button>
      <p class="muted small"><Link href="/login">Back to log in</Link></p>
    </AuthCard>
  );
}
