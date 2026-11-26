/** The wording and look of each email the server sends, with a live preview. */

import { useEffect, useState } from 'preact/hooks';
import { api, type EmailTemplate } from '../api';
import { Link } from '../router';
import { Button, Field, Loading, Notice, PageHeader, Select, TextArea, describe, useLoad } from '../ui';

interface TemplateInfo { label: string; description: string; vars: Record<string, string> }

export const TEMPLATES: Record<string, TemplateInfo> = {
  'test': { label: 'Test email', description: 'Sent from the Email page to check the mail settings.', vars: {} },
  'registration-verification': { label: 'Verify email address', description: 'Sent when someone registers, with a link to confirm their address.',
    vars: { name: 'Their name', verify_url: 'The verification link (lasts 24 hours)' } },
  'set-password': { label: 'Set a password', description: 'Sent once an address is verified, with a link to choose a password.',
    vars: { name: 'Their name', set_password_url: 'The link (lasts 24 hours)' } },
  'user-activated': { label: 'Account ready', description: 'Sent when a new account has its password. Contains the API key.',
    vars: { name: 'Their name', api_key: 'Their API key' } },
  'password-reset-required': { label: 'New password needed', description: 'Sent for a forgotten password, or when an administrator asks for a new one.',
    vars: { name: 'Their name', set_password_url: 'The link (lasts 24 hours)' } },
  '2fa-code': { label: 'Sign-in code', description: 'Sent when someone logs in on a device that isn\'t remembered.',
    vars: { name: 'Their name', code: 'The 6-digit code', expiry_minutes: 'Minutes until it expires' } },
  'key-rotated': { label: 'New API key', description: 'Sent when a key is replaced, by its owner or an administrator.',
    vars: { name: 'Their name', identifier: 'Their email address', api_key: 'The new key' } },
};

const SAMPLE: Record<string, string> = {
  name: 'Alex Example', verify_url: 'https://example.com/verify?t=…', set_password_url: 'https://example.com/set-password?t=…',
  api_key: 'k7Hq…example…P2', code: '482913', expiry_minutes: '10', identifier: 'alex@example.com',
};

const APPEARANCE = ['bg_color', 'panel_color', 'text_color', 'content_width', 'header_align'] as const;

export function EmailTemplates() {
  return (
    <>
      <p class="crumbs"><Link href="/email">Email</Link></p>
      <PageHeader title="Email templates" intro="What each email says and how it looks." />
      <table class="table">
        <tbody>
          {Object.entries(TEMPLATES).map(([name, info]) => (
            <tr key={name}>
              <td><Link href={`/email/templates/${name}`} class="row-link">{info.label}</Link></td>
              <td class="muted">{info.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** Same layout as email_service.py's _render_template_html, for the preview. */
function render(t: EmailTemplate, vars: Record<string, string>): string {
  const fill = (s: string) => s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? vars[k]! : m));
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  const link = (s: string) => s.replace(/(https?:\/\/[^\s<>"']+)/g, '<a href="$1" style="color:#2E75B6;word-break:break-all;">$1</a>');
  const body = fill(t.body_text).split('\n')
    .map((line) => (line.trim() ? `<p style="margin:0 0 12px;">${link(esc(line))}</p>` : '<br>')).join('');
  const w = Number(t.content_width) || 600;
  return `<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:${t.bg_color};font-family:Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:${t.bg_color};padding:32px 16px;"><tr><td align="center">
<table width="100%" cellpadding="0" cellspacing="0" style="max-width:${w}px;background:${t.panel_color};border-radius:6px;overflow:hidden;">
<tr><td style="padding:28px 32px 20px;border-bottom:1px solid #e0e0e0;text-align:${t.header_align};"><span style="font-size:18px;font-weight:600;color:${t.text_color};">${esc(fill(t.header_text))}</span></td></tr>
<tr><td style="padding:28px 32px;color:${t.text_color};font-size:14px;line-height:1.6;">${body}</td></tr>
<tr><td style="padding:16px 32px;background:${t.panel_color};color:#888;font-size:12px;border-top:1px solid #e0e0e0;">${esc(fill(t.footer_text))}</td></tr>
</table></td></tr></table></body></html>`;
}

export function EmailTemplateEditor({ name }: { name: string }) {
  const info = TEMPLATES[name];
  const [loaded, reload] = useLoad(() => api<EmailTemplate>(`/admin/email-templates/${name}`), [name]);
  const [draft, setDraft] = useState<EmailTemplate | null>(null);
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (loaded.state === 'ready') setDraft({ ...loaded.data });
  }, [loaded]);

  if (!info) return <Notice tone="error">There's no email template called "{name}".</Notice>;

  const run = async (action: () => Promise<string>) => {
    setBusy(true);
    setMessage(null);
    try {
      setMessage({ tone: 'success', text: await action() });
      reload();
    } catch (e) {
      setMessage({ tone: 'error', text: describe(e) });
    } finally {
      setBusy(false);
    }
  };

  const appearance = (t: EmailTemplate) => Object.fromEntries(APPEARANCE.map((k) => [k, t[k]]));

  return (
    <>
      <p class="crumbs"><Link href="/email">Email</Link> / <Link href="/email/templates">Templates</Link></p>
      <PageHeader title={info.label} intro={info.description} />
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Loading result={loaded}>{() => {
        if (!draft) return null;
        const set = (key: keyof EmailTemplate) => (value: string) => setDraft({ ...draft, [key]: key === 'content_width' ? Number(value) : value });
        return (
          <div class="columns">
            <div class="card form-card">
              <Field label="Subject" value={draft.subject} onValue={set('subject')} />
              <Field label="Heading" value={draft.header_text} onValue={set('header_text')} />
              <TextArea label="Message" rows={10} value={draft.body_text} onValue={set('body_text')} />
              <Field label="Footer" value={draft.footer_text} onValue={set('footer_text')} />
              {Object.keys(info.vars).length > 0 && (
                <div class="muted small">
                  These are filled in when the email is sent:
                  <ul>
                    {Object.entries(info.vars).map(([k, v]) => <li key={k}><code>{`{${k}}`}</code> — {v}</li>)}
                  </ul>
                </div>
              )}
              <h3 class="card__title">Look</h3>
              <div class="swatch-row">
                <label class="field"><span>Background</span><input type="color" value={draft.bg_color} onInput={(e) => set('bg_color')(e.currentTarget.value)} /></label>
                <label class="field"><span>Panel</span><input type="color" value={draft.panel_color} onInput={(e) => set('panel_color')(e.currentTarget.value)} /></label>
                <label class="field"><span>Text</span><input type="color" value={draft.text_color} onInput={(e) => set('text_color')(e.currentTarget.value)} /></label>
                <Field label="Width (px)" type="number" value={String(draft.content_width)} onValue={set('content_width')} />
                <Select label="Heading" value={draft.header_align} onValue={set('header_align')} options={[['left', 'Left'], ['center', 'Centred']]} />
              </div>
              <div class="actions">
                <Button variant="primary" busy={busy} onClick={() => void run(async () => {
                  await api(`/admin/email-templates/${name}`, 'POST', draft);
                  return 'Template saved.';
                })}>Save</Button>
                <Button busy={busy} onClick={() => void run(async () => {
                  await api(`/admin/email-templates/${name}`, 'POST', draft);
                  for (const other of Object.keys(TEMPLATES)) {
                    if (other !== name) await api(`/admin/email-templates/${other}`, 'POST', appearance(draft));
                  }
                  return 'Saved, and every email now uses this look.';
                })}>Save and use this look for every email</Button>
                <Button busy={busy} onClick={() => {
                  if (confirm('Go back to the original wording and look for this email?')) {
                    void run(async () => {
                      await api(`/admin/email-templates/${name}/reset`, 'POST');
                      return 'Back to the original.';
                    });
                  }
                }}>Reset</Button>
              </div>
            </div>
            <div class="card">
              <h2 class="card__title">Preview</h2>
              <p class="muted small">Subject: {draft.subject.replace(/\{(\w+)\}/g, (m, k: string) => SAMPLE[k] ?? m)}</p>
              <iframe class="email-preview" title="Email preview" sandbox="" srcdoc={render(draft, SAMPLE)} />
            </div>
          </div>
        );
      }}</Loading>
    </>
  );
}
