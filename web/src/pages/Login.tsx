import { useState } from 'preact/hooks';
import { api, type LoginResult, type Session } from '../api';
import { config } from '../config';
import { Link, navigate } from '../router';
import { Button, Checkbox, Field, Notice, useSubmit } from '../ui';
import { AuthCard } from './AuthCard';

export function Login({ onDone }: { onDone: (session: Session) => void }) {
  const [step, setStep] = useState<'password' | 'code'>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [remember, setRemember] = useState(false);

  const signIn = useSubmit(async () => {
    const result = await api<LoginResult>('/auth/login', 'POST', { email, password });
    if ('session' in result) onDone(result.session);
    else if ('2fa_required' in result) setStep('code');
    else navigate(`/set-password?email=${encodeURIComponent(result.email)}&required=1`);
  });

  const verify = useSubmit(async () => {
    const result = await api<{ session: Session }>('/auth/login/2fa', 'POST', { email, code, remember_device: remember });
    onDone(result.session);
  });

  if (step === 'code') {
    return (
      <AuthCard title="Check your email" onSubmit={verify.onSubmit}>
        <p class="muted">We've sent a verification code to <strong>{email}</strong>.</p>
        {verify.error && <Notice tone="error">{verify.error}</Notice>}
        <Field label="Verification code" value={code} onValue={setCode} autoComplete="one-time-code" required />
        <Checkbox label={`Remember this device for ${config.trusted_device_days} days`} checked={remember} onChecked={setRemember} />
        <Button type="submit" variant="primary" busy={verify.busy}>Verify</Button>
        <p class="muted small">
          <a href="/login" onClick={(e) => { e.preventDefault(); setStep('password'); setCode(''); }}>Start again</a>
        </p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Log in" onSubmit={signIn.onSubmit}>
      {signIn.error && <Notice tone="error">{signIn.error}</Notice>}
      <Field label="Email" type="email" value={email} onValue={setEmail} autoComplete="username" required />
      <Field label="Password" type="password" value={password} onValue={setPassword} autoComplete="current-password" required />
      <Button type="submit" variant="primary" busy={signIn.busy}>Log in</Button>
      <p class="muted small">
        <Link href="/forgot-password">Forgotten your password?</Link>
        {config.allow_registration && <> · <Link href="/register">Create an account</Link></>}
      </p>
    </AuthCard>
  );
}
