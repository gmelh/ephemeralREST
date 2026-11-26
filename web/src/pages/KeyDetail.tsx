/** One key: rate limits, output, federated services, admin access, status, password, deletion. */

import { useEffect, useState } from 'preact/hooks';
import { api, type FederatedService, type KeyRecord } from '../api';
import { config } from '../config';
import { Link, navigate } from '../router';
import { useSession } from '../session';
import { Button, Checkbox, CopyText, Field, Loading, Notice, PageHeader, describe, formatDate, useLoad } from '../ui';

type KeyWithServices = KeyRecord & { services: string[] };
type Message = { tone: 'success' | 'error'; text: string } | null;

const toInput = (n: number | null) => (n === null || n === undefined ? '' : String(n));
const fromInput = (s: string) => (s.trim() === '' ? null : Number.parseInt(s, 10));

export function KeyDetail({ id }: { id: number }) {
  const { session } = useSession();
  const [key, reload] = useLoad(() => api<KeyWithServices>(`/admin/keys/${id}`), [id]);
  const [services] = useLoad(() => api<{ services: FederatedService[] }>('/admin/federated-services'), []);
  const [message, setMessage] = useState<Message>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [limits, setLimits] = useState({ rpm: '', rph: '', rpd: '' });
  const [granted, setGranted] = useState<string[]>([]);

  useEffect(() => {
    if (key.state !== 'ready') return;
    setLimits({ rpm: toInput(key.data.rate_per_minute), rph: toInput(key.data.rate_per_hour), rpd: toInput(key.data.rate_per_day) });
    setGranted(key.data.services ?? []);
  }, [key]);

  /** Run one change, show how it went, and reload the key. */
  const act = async (name: string, run: () => Promise<string | void>, confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(name);
    setMessage(null);
    try {
      const text = await run();
      if (text) setMessage({ tone: 'success', text });
      reload();
    } catch (e) {
      setMessage({ tone: 'error', text: describe(e) });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Loading result={key}>{() => {
      if (key.state !== 'ready') return null;
      const k = key.data;
      const isSelf = k.id === session.user.id;
      return (
        <>
          <p class="crumbs"><Link href="/keys">Keys</Link></p>
          <PageHeader
            title={k.name || k.identifier}
            intro={k.identifier}
            actions={<Link href={`/keys/${k.id}/output`} class="btn">Output settings</Link>}
          />
          {message && <Notice tone={message.tone}>{message.text}</Notice>}
          {newKey && (
            <div class="card">
              <h2 class="card__title">New API key</h2>
              <Notice tone="success">Shown only once, and emailed to {k.identifier}.</Notice>
              <CopyText text={newKey} />
            </div>
          )}
          <div class="columns">
            <div>
              <div class="card">
                <h2 class="card__title">Key</h2>
                <dl class="facts">
                  <div><dt>Prefix</dt><dd class="mono">{k.key_prefix}</dd></div>
                  <div><dt>Status</dt><dd><span class={`badge ${k.active ? 'badge--on' : 'badge--off'}`}>{k.active ? 'active' : 'disabled'}</span></dd></div>
                  <div><dt>Role</dt><dd>{k.admin ? 'administrator' : 'user'}</dd></div>
                  <div><dt>Created</dt><dd>{formatDate(k.created_at)}</dd></div>
                  <div><dt>Changed</dt><dd>{formatDate(k.updated_at)}</dd></div>
                </dl>
              </div>
              <form class="card" onSubmit={(e) => {
                e.preventDefault();
                void act('limits', async () => {
                  await api(`/admin/keys/${k.id}/limits`, 'POST', {
                    rate_per_minute: fromInput(limits.rpm), rate_per_hour: fromInput(limits.rph), rate_per_day: fromInput(limits.rpd),
                  });
                  return 'Rate limits saved.';
                });
              }}>
                <h2 class="card__title">Rate limits</h2>
                <p class="muted small">Leave a box empty to use the <Link href="/rate-limits">default</Link>.
                  {k.admin ? ' Administrators aren\'t rate limited.' : ''}</p>
                <div class="form-grid">
                  <Field label="Per minute" type="number" value={limits.rpm} onValue={(v) => setLimits({ ...limits, rpm: v })} placeholder="default" />
                  <Field label="Per hour" type="number" value={limits.rph} onValue={(v) => setLimits({ ...limits, rph: v })} placeholder="default" />
                  <Field label="Per day" type="number" value={limits.rpd} onValue={(v) => setLimits({ ...limits, rpd: v })} placeholder="default" />
                </div>
                <div class="actions"><Button type="submit" variant="primary" busy={busy === 'limits'}>Save limits</Button></div>
              </form>
              <div class="card">
                <h2 class="card__title">Federated services</h2>
                <Loading result={services}>{() => {
                  if (services.state !== 'ready') return null;
                  const all = services.data.services;
                  const unknown = granted.filter((slug) => !all.some((s) => s.slug === slug));
                  if (!all.length && !unknown.length) {
                    return <p class="muted">No services are registered. <Link href="/services">Register one</Link> to grant access to it.</p>;
                  }
                  const toggle = (slug: string, on: boolean) => setGranted(on ? [...granted, slug].sort() : granted.filter((s) => s !== slug));
                  return (
                    <>
                      <p class="muted small">Which other services in your federation accept this key.</p>
                      {all.map((s) => (
                        <Checkbox key={s.slug} checked={granted.includes(s.slug)} onChecked={(on) => toggle(s.slug, on)}
                                  label={<>{s.display_name} <span class="muted mono">{s.slug}</span>{!s.active && <span class="badge badge--off" style="margin-left:6px">inactive</span>}</>}
                                  hint={s.description ?? undefined} />
                      ))}
                      {unknown.map((slug) => (
                        <Checkbox key={slug} checked onChecked={(on) => toggle(slug, on)} label={<span class="mono">{slug}</span>} hint="Not in the services list." />
                      ))}
                      <div class="actions">
                        <Button variant="primary" busy={busy === 'services'} onClick={() => void act('services', async () => {
                          await api(`/admin/keys/${k.id}/services`, 'PUT', { services: granted });
                          return 'Service access saved.';
                        })}>Save access</Button>
                      </div>
                    </>
                  );
                }}</Loading>
              </div>
            </div>
            <div>
              <div class="card">
                <h2 class="card__title">New key</h2>
                <p class="muted">Make a new key for this account. The current one stops working straight away; the new one is emailed to them.</p>
                <div class="actions">
                  <Button busy={busy === 'rotate'} onClick={() => void act('rotate', async () => {
                    const r = await api<{ api_key: string }>(`/admin/keys/${k.id}/rotate`, 'POST');
                    setNewKey(r.api_key);
                  }, `Make a new key for ${k.identifier}? Their current key stops working straight away.`)}>Make a new key</Button>
                </div>
              </div>
              <div class="card">
                <h2 class="card__title">Password</h2>
                <p class="muted">Ask them to choose a new password: they're emailed a link and can't log in until they do.</p>
                <div class="actions">
                  <Button busy={busy === 'reset'} onClick={() => void act('reset', async () => {
                    const r = await api<{ email_sent: boolean }>(`/admin/keys/${k.id}/force-password-reset`, 'POST');
                    return r.email_sent ? `A link has been emailed to ${k.identifier}.` : "Done, but the email couldn't be sent — check the email settings.";
                  }, `Require ${k.identifier} to set a new password?`)}>Require a new password</Button>
                </div>
              </div>
              {(config.allow_admin_promotion || k.admin) && (
                <div class="card">
                  <h2 class="card__title">Administrator</h2>
                  {isSelf
                    ? <p class="muted">You can't change your own administrator access.</p>
                    : (
                      <>
                        <p class="muted">{k.admin ? 'This account can manage keys and settings.' : 'Give this account access to everything here.'}</p>
                        <div class="actions">
                          <Button variant={k.admin ? 'danger' : 'plain'} busy={busy === 'admin'} onClick={() => void act('admin', async () => {
                            await api(`/admin/keys/${k.id}/set-admin`, 'POST', { admin: !k.admin });
                            return k.admin ? 'Administrator access removed.' : 'Now an administrator.';
                          }, k.admin ? `Remove administrator access from ${k.identifier}?` : `Make ${k.identifier} an administrator?`)}>
                            {k.admin ? 'Remove administrator access' : 'Make administrator'}
                          </Button>
                        </div>
                      </>
                    )}
                </div>
              )}
              <div class="card">
                <h2 class="card__title">Status</h2>
                <p class="muted">{k.active ? 'Disabling stops the key working, and the account logging in, until it is enabled again.' : 'This key is disabled (or its email address was never verified).'}</p>
                <div class="actions">
                  {k.active
                    ? <Button variant="danger" busy={busy === 'status'} disabled={isSelf} onClick={() => void act('status', async () => {
                        await api(`/admin/keys/${k.id}/disable`, 'POST');
                        return 'Key disabled.';
                      }, `Disable ${k.identifier}? The key stops working straight away.`)}>Disable</Button>
                    : <Button busy={busy === 'status'} onClick={() => void act('status', async () => {
                        await api(`/admin/keys/${k.id}/enable`, 'POST');
                        return 'Key enabled.';
                      })}>Enable</Button>}
                  <Button variant="danger" busy={busy === 'delete'} disabled={isSelf} onClick={() => void act('delete', async () => {
                    await api(`/admin/keys/${k.id}`, 'DELETE');
                    navigate('/keys');
                  }, `Delete ${k.identifier} permanently? This can't be undone.`)}>Delete</Button>
                </div>
              </div>
            </div>
          </div>
        </>
      );
    }}</Loading>
  );
}
