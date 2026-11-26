/** How the server sends email (sign-in codes, verification and password links, new keys). */

import { useEffect, useState } from 'preact/hooks';
import { api, type SmtpConfig } from '../api';
import { Link } from '../router';
import { useSession } from '../session';
import { Button, Field, Loading, Notice, PageHeader, Select, describe, useLoad, useSubmit } from '../ui';

type Security = 'tls' | 'ssl' | 'none';

export function Email() {
  const { session } = useSession();
  const [loaded, reload] = useLoad(() => api<{ config: SmtpConfig; configured: boolean }>('/admin/smtp'), []);
  const [form, setForm] = useState({ host: '', port: '587', user: '', password: '', from_addr: '', admin_email: '' });
  const [security, setSecurity] = useState<Security>('tls');
  const [saved, setSaved] = useState(false);
  const [testTo, setTestTo] = useState(session.user.identifier);
  const [test, setTest] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (loaded.state !== 'ready') return;
    const c = loaded.data.config;
    setForm({ host: c.host ?? '', port: c.port || '587', user: c.user ?? '', password: '', from_addr: c.from_addr ?? '', admin_email: c.admin_email ?? '' });
    setSecurity(c.use_ssl === 'true' ? 'ssl' : c.use_tls === 'false' ? 'none' : 'tls');
  }, [loaded]);

  const field = (key: keyof typeof form) => (value: string) => setForm({ ...form, [key]: value });

  const save = useSubmit(async () => {
    setSaved(false);
    const body: Record<string, string> = {
      host: form.host.trim(), port: form.port.trim() || '587', user: form.user.trim(),
      from_addr: form.from_addr.trim(), admin_email: form.admin_email.trim(),
      use_tls: security === 'tls' ? 'true' : 'false', use_ssl: security === 'ssl' ? 'true' : 'false',
    };
    if (form.password) body.password = form.password;
    await api('/admin/smtp', 'POST', body);
    setSaved(true);
    reload();
  });

  const sendTest = async () => {
    setTesting(true);
    setTest(null);
    try {
      const r = await api<{ message: string }>('/admin/smtp/test', 'POST', { to: testTo });
      setTest({ tone: 'success', text: r.message });
    } catch (e) {
      setTest({ tone: 'error', text: describe(e) });
    } finally {
      setTesting(false);
    }
  };

  const clear = async () => {
    if (!confirm('Remove the email settings? Sign-in codes and links will stop being sent.')) return;
    await api('/admin/smtp', 'DELETE').catch(() => undefined);
    reload();
  };

  return (
    <>
      <PageHeader title="Email" intro="The mail server used for sign-in codes, verification and password links, and new API keys."
                  actions={<Link href="/email/templates" class="btn">Email templates</Link>} />
      <Loading result={loaded}>{() => loaded.state === 'ready' && (
        <div class="columns">
          <form class="card form-card" onSubmit={save.onSubmit}>
            <h2 class="card__title">Mail server (SMTP)</h2>
            {!loaded.data.configured && (
              <Notice>Not set up yet. Until it is, administrators log in without a code, and other people can't get one.</Notice>
            )}
            {save.error && <Notice tone="error">{save.error}</Notice>}
            {saved && <Notice tone="success">Email settings saved.</Notice>}
            <div class="form-grid">
              <Field label="Server" value={form.host} onValue={field('host')} placeholder="smtp.example.com" required />
              <Field label="Port" type="number" value={form.port} onValue={field('port')} />
              <Field label="Username" value={form.user} onValue={field('user')} autoComplete="off" required />
              <Field label="Password" type="password" value={form.password} onValue={field('password')} autoComplete="new-password"
                     placeholder={loaded.data.config.password_set ? 'Saved — leave empty to keep it' : ''} />
            </div>
            <Select label="Security" value={security} onValue={(v) => setSecurity(v as Security)}
                    options={[['tls', 'STARTTLS (usually port 587)'], ['ssl', 'SSL/TLS (usually port 465)'], ['none', 'None']]} />
            <Field label="From address" type="email" value={form.from_addr} onValue={field('from_addr')}
                   hint="Optional — the username is used if it's empty." />
            <Field label="Administrator's email" type="email" value={form.admin_email} onValue={field('admin_email')}
                   hint="Optional — told about new registrations." />
            <div class="actions">
              <Button type="submit" variant="primary" busy={save.busy}>Save</Button>
              {loaded.data.configured && <Button variant="danger" onClick={() => void clear()}>Remove</Button>}
            </div>
          </form>
          <div class="card">
            <h2 class="card__title">Send a test email</h2>
            <p class="muted">Uses the saved settings and the Test template.</p>
            {test && <Notice tone={test.tone}>{test.text}</Notice>}
            <Field label="Send to" type="email" value={testTo} onValue={setTestTo} />
            <div class="actions"><Button busy={testing} disabled={!loaded.data.configured} onClick={() => void sendTest()}>Send test</Button></div>
          </div>
        </div>
      )}</Loading>
    </>
  );
}
