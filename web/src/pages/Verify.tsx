/** The link in the registration email lands here (?t=TOKEN). */

import { useEffect, useState } from 'preact/hooks';
import { api } from '../api';
import { Link } from '../router';
import { describe, Notice } from '../ui';
import { AuthCard, query } from './AuthCard';

export function Verify() {
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    const token = query('t');
    if (!token) {
      setResult({ ok: false, message: 'This link is missing its verification code.' });
      return;
    }
    api<{ message: string }>(`/register/verify?t=${encodeURIComponent(token)}`)
      .then((r) => setResult({ ok: true, message: r.message }))
      .catch((e: unknown) => setResult({ ok: false, message: describe(e) }));
  }, []);

  if (!result) return <AuthCard title="Verifying…"><p class="muted">One moment.</p></AuthCard>;
  return (
    <AuthCard title={result.ok ? 'Email verified' : "We couldn't verify your email"}>
      <Notice tone={result.ok ? 'success' : 'error'}>{result.message}</Notice>
      <p class="muted small"><Link href="/login">Go to log in</Link></p>
    </AuthCard>
  );
}
