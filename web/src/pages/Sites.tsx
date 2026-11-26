/**
 * Sites: the apps (hosted AstrologyStudio sites, say) that use ephemeralREST through a domain
 * key. The list, a new site (its key is shown once), the defaults new sites start with, and one
 * site's page: sign-up addresses, email delivery, limits, its accounts, suspend, a new key, remove.
 */

import { useEffect, useState } from 'preact/hooks';
import { api, type AdminAction, type Site, type SiteDefaults, type SiteDetail as Detail } from '../api';
import { Link, navigate } from '../router';
import {
  Button, Checkbox, CopyText, EmptyState, Field, Loading, Notice, PageHeader, TextArea, count, describe, formatDate,
  useLoad, useSubmit,
} from '../ui';

const toInput = (n: number | null) => (n === null || n === undefined ? '' : String(n));
const fromInput = (s: string) => (s.trim() === '' ? null : Math.max(0, Number.parseInt(s, 10) || 0));
const limit = (n: number | null) => (n === null ? <span class="muted">default</span> : n.toLocaleString());

function delivery(site: Site): string {
  if (!site.email_delivery.allowed) return 'ephemeralREST';
  return site.email_delivery.mode === 'self' ? 'the site' : 'ephemeralREST (allowed to switch)';
}

export function Sites() {
  const [sites, reload] = useLoad(() => api<{ sites: Site[]; defaults: SiteDefaults }>('/admin/sites'), []);
  const [adding, setAdding] = useState(false);
  const [made, setMade] = useState<{ site: Site; api_key: string } | null>(null);
  const [search, setSearch] = useState('');

  return (
    <>
      <PageHeader
        title="Sites"
        intro="Apps that use ephemeralREST through a domain key, such as hosted AstrologyStudio sites. Each has its own key, the addresses its sign-up emails may link to, and the accounts made through it."
        actions={!adding && <Button variant="primary" onClick={() => { setAdding(true); setMade(null); }}>New site</Button>}
      />
      {made && (
        <div class="card">
          <h2 class="card__title">{made.site.domain}: its key</h2>
          <Notice tone="success">Shown only once. Give it to the site's owner: in AstrologyStudio it goes in Settings →
            ephemeralREST → Site key.</Notice>
          <CopyText text={made.api_key} />
          <div class="actions"><Link href={`/sites/${made.site.id}`} class="btn">Open the site</Link></div>
        </div>
      )}
      {adding && <NewSite onDone={(m) => { setAdding(false); setMade(m); reload(); }} onCancel={() => setAdding(false)} />}
      <Loading result={sites}>{() => {
        if (sites.state !== 'ready') return null;
        const term = search.trim().toLowerCase();
        const shown = sites.data.sites.filter((s) => !term || s.domain.includes(term) || s.name.toLowerCase().includes(term));
        return (
          <>
            {sites.data.sites.length > 5 && (
              <div class="toolbar">
                <input class="search" type="search" placeholder="Search by domain or name" value={search}
                       onInput={(e) => setSearch(e.currentTarget.value)} aria-label="Search sites" />
              </div>
            )}
            {!shown.length ? (
              <EmptyState title={term ? 'No sites match' : 'No sites yet'}>
                <p>Add one for each app that signs people up through ephemeralREST.</p>
              </EmptyState>
            ) : (
              <>
                <div class="table-scroll">
                  <table class="table">
                    <thead>
                      <tr><th>Domain</th><th>Name</th><th>Status</th><th>Accounts</th><th>Emails sent by</th><th>Per minute</th><th>Per day</th></tr>
                    </thead>
                    <tbody>
                      {shown.map((s) => (
                        <tr key={s.id}>
                          <td><Link href={`/sites/${s.id}`} class="row-link">{s.domain}</Link></td>
                          <td>{s.name}</td>
                          <td><span class={`badge ${s.active ? 'badge--on' : 'badge--off'}`}>{s.active ? 'active' : 'suspended'}</span></td>
                          <td>{s.accounts.toLocaleString()}</td>
                          <td>{delivery(s)}</td>
                          <td>{limit(s.rate_per_minute)}</td>
                          <td>{limit(s.rate_per_day)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p class="muted small">{count(shown.length, 'site')}</p>
              </>
            )}
            <Defaults current={sites.data.defaults} />
          </>
        );
      }}</Loading>
    </>
  );
}

function NewSite({ onDone, onCancel }: { onDone: (made: { site: Site; api_key: string }) => void; onCancel: () => void }) {
  const [domain, setDomain] = useState('');
  const [name, setName] = useState('');
  const { busy, error, onSubmit } = useSubmit(async () => {
    onDone(await api<{ site: Site; api_key: string }>('/admin/sites', 'POST', { domain, name: name || undefined }));
  });
  return (
    <form class="card form" onSubmit={onSubmit}>
      <h2 class="card__title">New site</h2>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Domain" value={domain} onValue={setDomain} required placeholder="astro.example.com" id="site-domain"
             hint="The site's address. Its sign-up emails may link to it (and its subdomains); you can add more after." />
      <Field label="Name (optional)" value={name} onValue={setName} id="site-name" placeholder="Star Charts" />
      <p class="muted small">It starts with the defaults below: email delivery and rate limits.</p>
      <div class="actions">
        <Button type="submit" variant="primary" busy={busy}>Make the site and its key</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function LimitFields({ value, onValue, prefix }: {
  value: { rpm: string; rph: string; rpd: string }; onValue: (v: { rpm: string; rph: string; rpd: string }) => void; prefix: string;
}) {
  return (
    <div class="form-grid">
      <Field label="Per minute" type="number" value={value.rpm} onValue={(v) => onValue({ ...value, rpm: v })} id={`${prefix}-rpm`} placeholder="default" />
      <Field label="Per hour" type="number" value={value.rph} onValue={(v) => onValue({ ...value, rph: v })} id={`${prefix}-rph`} placeholder="default" />
      <Field label="Per day" type="number" value={value.rpd} onValue={(v) => onValue({ ...value, rpd: v })} id={`${prefix}-rpd`} placeholder="default" />
    </div>
  );
}

/** What a new site starts with. */
function Defaults({ current }: { current: SiteDefaults }) {
  const [allowed, setAllowed] = useState(current.email_delivery_allowed);
  const [limits, setLimits] = useState({ rpm: toInput(current.rate_per_minute), rph: toInput(current.rate_per_hour), rpd: toInput(current.rate_per_day) });
  const [saved, setSaved] = useState(false);
  const { busy, error, onSubmit } = useSubmit(async () => {
    setSaved(false);
    await api('/admin/site-defaults', 'PUT', {
      email_delivery_allowed: allowed,
      rate_per_minute: fromInput(limits.rpm), rate_per_hour: fromInput(limits.rph), rate_per_day: fromInput(limits.rpd),
    });
    setSaved(true);
  });
  return (
    <form class="card form" onSubmit={onSubmit}>
      <h2 class="card__title">Defaults for new sites</h2>
      <p class="muted">A new site starts with these. Changing them doesn't change existing sites.</p>
      {error && <Notice tone="error">{error}</Notice>}
      {saved && <Notice tone="success">Saved.</Notice>}
      <Checkbox label="Allowed to send their own accounts' emails" checked={allowed} onChecked={(v) => { setAllowed(v); setSaved(false); }}
                hint="Sign-up links, login codes, resets and key notices for accounts made through the site go out from the site, with its branding, once it switches it on." />
      <LimitFields value={limits} onValue={(v) => { setLimits(v); setSaved(false); }} prefix="defaults" />
      <p class="muted small">Rate limits for the site's own key. Empty: the class default (Rate limits).</p>
      <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save defaults</Button></div>
    </form>
  );
}

export function SiteDetail({ id }: { id: number }) {
  const [site, reload] = useLoad(() => api<Detail>(`/admin/sites/${id}`), [id]);
  const [all] = useLoad(() => api<{ sites: Site[] }>('/admin/sites'), []);
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [origins, setOrigins] = useState('');
  const [limits, setLimits] = useState({ rpm: '', rph: '', rpd: '' });
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    if (site.state !== 'ready') return;
    setName(site.data.name);
    setOrigins(site.data.origins.join('\n'));
    setLimits({ rpm: toInput(site.data.rate_per_minute), rph: toInput(site.data.rate_per_hour), rpd: toInput(site.data.rate_per_day) });
  }, [site]);

  const act = async (label: string, run: () => Promise<string | void>, confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(label);
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
  const update = (body: Record<string, unknown>) => api<Site>(`/admin/sites/${id}`, 'PUT', body);

  return (
    <Loading result={site}>{() => {
      if (site.state !== 'ready') return null;
      const s = site.data;
      const others = all.state === 'ready' ? all.data.sites.filter((o) => o.id !== s.id) : [];
      return (
        <>
          <p class="crumbs"><Link href="/sites">Sites</Link></p>
          <PageHeader title={s.name || s.domain} intro={s.domain} />
          {message && <Notice tone={message.tone}>{message.text}</Notice>}
          {newKey && (
            <div class="card">
              <h2 class="card__title">The site's new key</h2>
              <Notice tone="success">Shown only once. The old key has stopped working: give this one to the site's owner.</Notice>
              <CopyText text={newKey} />
            </div>
          )}
          <div class="columns">
            <div>
              <div class="card">
                <h2 class="card__title">Site</h2>
                <dl class="facts">
                  <div><dt>Key prefix</dt><dd class="mono">{s.key_prefix}</dd></div>
                  <div><dt>Status</dt><dd><span class={`badge ${s.active ? 'badge--on' : 'badge--off'}`}>{s.active ? 'active' : 'suspended'}</span></dd></div>
                  <div><dt>Accounts</dt><dd>{s.accounts.toLocaleString()}</dd></div>
                  <div><dt>Created</dt><dd>{formatDate(s.created_at)}</dd></div>
                </dl>
                <form class="form" onSubmit={(e) => { e.preventDefault(); void act('name', async () => { await update({ name }); return 'Saved.'; }); }}>
                  <Field label="Name" value={name} onValue={setName} id="site-detail-name" required />
                  <div class="actions"><Button type="submit" busy={busy === 'name'}>Save name</Button></div>
                </form>
              </div>
              <div class="card">
                <h2 class="card__title">Sign-up addresses</h2>
                <p class="muted">Where this site's sign-up and password emails may link to: https on {s.domain} or its
                  subdomains (localhost for testing). One a line. The site adds its own when its owner saves the key.</p>
                <form class="form" onSubmit={(e) => { e.preventDefault(); void act('origins', async () => {
                  await update({ origins: origins.split(/\s+/).map((o) => o.trim()).filter(Boolean) });
                  return 'Saved.';
                }); }}>
                  <TextArea label="Addresses" value={origins} onValue={setOrigins} id="site-origins" rows={3} />
                  <div class="actions"><Button type="submit" busy={busy === 'origins'}>Save addresses</Button></div>
                </form>
              </div>
              <div class="card">
                <h2 class="card__title">Email</h2>
                <Checkbox label="Allowed to send its own accounts' emails" checked={s.email_delivery.allowed}
                          onChecked={(allowed) => void act('delivery', async () => {
                            await update({ email_delivery_allowed: allowed });
                            return allowed ? 'Allowed. The site switches it on from its own settings.' : 'Stopped: ephemeralREST sends them again.';
                          })}
                          hint={s.email_delivery.mode === 'self'
                            ? 'The site sends them now, with its own branding.'
                            : 'ephemeralREST sends them now.'} />
              </div>
              <div class="card">
                <h2 class="card__title">Rate limits for the site's key</h2>
                <form class="form" onSubmit={(e) => { e.preventDefault(); void act('limits', async () => {
                  await update({ rate_per_minute: fromInput(limits.rpm), rate_per_hour: fromInput(limits.rph), rate_per_day: fromInput(limits.rpd) });
                  return 'Saved.';
                }); }}>
                  <LimitFields value={limits} onValue={setLimits} prefix="site" />
                  <p class="muted small">Empty: the class default. These limit calls made with the site's own key, not its people's.</p>
                  <div class="actions"><Button type="submit" busy={busy === 'limits'}>Save limits</Button></div>
                </form>
              </div>
            </div>
            <div>
              <div class="card">
                <h2 class="card__title">Accounts made through this site</h2>
                {s.account_list.length === 0 ? <p class="muted">None yet.</p> : (
                  <div class="table-scroll">
                    <table class="table">
                      <thead><tr><th>Email</th><th>Name</th><th>Status</th><th>Joined</th><th>Move to</th></tr></thead>
                      <tbody>
                        {s.account_list.map((a) => (
                          <tr key={a.id}>
                            <td><Link href={`/keys/${a.id}`} class="row-link">{a.identifier}</Link></td>
                            <td>{a.name}</td>
                            <td>{!a.has_password ? <span class="badge">not set up</span>
                              : <span class={`badge ${a.active ? 'badge--on' : 'badge--off'}`}>{a.active ? 'active' : 'disabled'}</span>}</td>
                            <td>{formatDate(a.created_at)}</td>
                            <td>
                              <select aria-label={`Move ${a.identifier} to another site`} value=""
                                      onChange={(e) => {
                                        const to = e.currentTarget.value;
                                        e.currentTarget.value = '';
                                        if (!to) return;
                                        const home = to === 'none' ? null : Number(to);
                                        void act(`move-${a.id}`, async () => {
                                          await api(`/admin/keys/${a.id}/home`, 'PUT', { home_key_id: home });
                                          return home === null ? `${a.identifier} no longer belongs to a site.` : `${a.identifier} moved.`;
                                        });
                                      }}>
                                <option value="">—</option>
                                {others.map((o) => <option key={o.id} value={String(o.id)}>{o.domain}</option>)}
                                <option value="none">No site</option>
                              </select>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                <p class="muted small">Moving an account changes which site may send its emails. Its key and charts don't change.</p>
              </div>
              <div class="card">
                <h2 class="card__title">Recent changes</h2>
                <ActionList rows={s.activity} compact />
                <div class="actions"><Link href="/activity" class="btn">All activity</Link></div>
              </div>
              <div class="card">
                <h2 class="card__title">Key and status</h2>
                <div class="actions">
                  <Button busy={busy === 'active'} onClick={() => void act('active', async () => {
                    await update({ active: !s.active });
                    return s.active ? 'Suspended: its key stops working until you re-enable it.' : 'Re-enabled.';
                  }, s.active ? `Suspend ${s.domain}? Its key stops working at once; its people can still use their own keys.` : undefined)}>
                    {s.active ? 'Suspend' : 'Re-enable'}
                  </Button>
                  <Button busy={busy === 'rotate'} onClick={() => void act('rotate', async () => {
                    const r = await api<{ api_key: string }>(`/admin/sites/${id}/rotate`, 'POST');
                    setNewKey(r.api_key);
                  }, `Make a new key for ${s.domain}? The old one stops working at once, until its owner saves the new one.`)}>
                    Make a new key
                  </Button>
                  <Button variant="danger" busy={busy === 'delete'} onClick={() => {
                    if (!confirm(`Remove ${s.domain}? Its key stops working, and its ${count(s.accounts, 'account')} belong to no site (they keep their keys and charts).`)) return;
                    void api(`/admin/sites/${id}`, 'DELETE').then(() => navigate('/sites')).catch((e: unknown) => setMessage({ tone: 'error', text: describe(e) }));
                  }}>Remove site</Button>
                </div>
              </div>
            </div>
          </div>
        </>
      );
    }}</Loading>
  );
}

const ACTIONS: Record<string, string> = {
  create_site: 'Site created', update_site: 'Site changed', rotate_site: 'New site key', delete_site: 'Site removed',
  set_defaults: 'Site defaults changed', set_link_origins: 'Sign-up addresses changed',
  set_email_delivery: 'Email delivery changed', set_account_home: 'Account moved',
  disable_key: 'Key disabled', enable_key: 'Key enabled', rotate_key: 'Key replaced', delete_key: 'Key deleted',
  set_key_limits: 'Limits changed', set_key_output: 'Output changed', set_key_admin: 'Administrator access changed',
  force_password_reset: 'Password reset required', set_smtp: 'Email settings changed', clear_smtp: 'Email settings removed',
  set_class_limits: 'Class limits changed', set_email_template: 'Email template changed', reset_email_template: 'Email template reset',
};

export function actionLabel(action: string): string {
  return ACTIONS[action] ?? action.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

export function ActionList({ rows, showTarget = false, compact = false }: {
  rows: AdminAction[]; showTarget?: boolean; compact?: boolean;
}) {
  if (!rows.length) return <p class="muted">Nothing yet.</p>;
  if (compact) {
    return (
      <ul class="action-list">
        {rows.map((r) => (
          <li key={r.id}>
            <strong>{actionLabel(r.action)}</strong>
            <span class="muted small"> · {formatDate(r.at)} · {r.admin_email || '—'}</span>
            {r.detail && <div class="small mono action-list__detail">{r.detail}</div>}
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div class="table-scroll">
      <table class="table">
        <thead><tr><th>When</th><th>Who</th><th>What</th>{showTarget && <th>About</th>}<th>Details</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{formatDate(r.at)}</td>
              <td>{r.admin_email || '—'}</td>
              <td>{actionLabel(r.action)}</td>
              {showTarget && (
                <td>{r.target_type === 'site' && r.target_id ? <Link href={`/sites/${r.target_id}`}>site {r.target_id}</Link>
                  : r.target_type === 'key' && r.target_id ? <Link href={`/keys/${r.target_id}`}>key {r.target_id}</Link>
                  : r.target_type ? `${r.target_type}${r.target_id ? ` ${r.target_id}` : ''}` : '—'}</td>
              )}
              <td class="small mono">{r.detail || ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
