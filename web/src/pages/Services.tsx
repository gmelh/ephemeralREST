/** Federated services: other services in your stack that accept ephemeralREST keys. */

import { useState } from 'preact/hooks';
import { api, type FederatedService } from '../api';
import { Link } from '../router';
import { Button, Checkbox, EmptyState, Field, Loading, Notice, PageHeader, describe, useLoad, useSubmit } from '../ui';

const blank = { slug: '', display_name: '', description: '', base_url: '' };

export function Services() {
  const [loaded, reload] = useLoad(() => api<{ services: FederatedService[] }>('/admin/federated-services'), []);
  const [form, setForm] = useState(blank);
  const [editing, setEditing] = useState<FederatedService | null>(null);
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const add = useSubmit(async () => {
    await api('/admin/federated-services', 'POST', {
      slug: form.slug.trim(), display_name: form.display_name.trim(),
      description: form.description.trim() || null, base_url: form.base_url.trim() || null,
    });
    setForm(blank);
    setMessage({ tone: 'success', text: 'Service added. Grant keys access to it from each key\'s page.' });
    reload();
  });

  const update = useSubmit(async () => {
    if (!editing) return;
    await api(`/admin/federated-services/${editing.id}`, 'PUT', {
      display_name: editing.display_name, description: editing.description || null,
      base_url: editing.base_url || null, active: Boolean(editing.active),
    });
    setEditing(null);
    setMessage({ tone: 'success', text: 'Service saved.' });
    reload();
  });

  const remove = async (s: FederatedService) => {
    if (!confirm(`Delete ${s.display_name}? (Turning it off instead keeps everyone's access for later.)`)) return;
    const grants = confirm(`Also remove every key's access to "${s.slug}"? Choose Cancel to leave their access in place.`);
    try {
      await api(`/admin/federated-services/${s.id}${grants ? '?remove_grants=1' : ''}`, 'DELETE');
      setMessage({ tone: 'success', text: `${s.display_name} deleted.` });
      reload();
    } catch (e) {
      setMessage({ tone: 'error', text: describe(e) });
    }
  };

  return (
    <>
      <PageHeader title="Federated services" intro="Other services that check ephemeralREST keys. A key works with a service once it's been granted access on the key's page."
                  actions={<Link href="/services/test" class="btn">Test a service</Link>} />
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Loading result={loaded}>{() => {
        if (loaded.state !== 'ready') return null;
        const services = loaded.data.services;
        return services.length ? (
          <table class="table">
            <thead><tr><th>Service</th><th>Slug</th><th>Address</th><th>Status</th><th /></tr></thead>
            <tbody>
              {services.map((s) => editing?.id === s.id ? (
                <tr key={s.id}>
                  <td colSpan={5}>
                    <form class="form" onSubmit={update.onSubmit}>
                      {update.error && <Notice tone="error">{update.error}</Notice>}
                      <div class="form-grid">
                        <Field label="Name" value={editing.display_name} onValue={(v) => setEditing({ ...editing, display_name: v })} required />
                        <Field label="Address" type="url" value={editing.base_url ?? ''} onValue={(v) => setEditing({ ...editing, base_url: v })} />
                      </div>
                      <Field label="Description" value={editing.description ?? ''} onValue={(v) => setEditing({ ...editing, description: v })} />
                      <Checkbox label="Active" checked={Boolean(editing.active)} onChecked={(v) => setEditing({ ...editing, active: v })}
                                hint="An inactive service keeps its grants but is hidden from new grants." />
                      <div class="actions">
                        <Button type="submit" variant="primary" busy={update.busy}>Save</Button>
                        <Button onClick={() => setEditing(null)}>Cancel</Button>
                      </div>
                    </form>
                  </td>
                </tr>
              ) : (
                <tr key={s.id}>
                  <td><strong>{s.display_name}</strong>{s.description && <div class="muted small">{s.description}</div>}</td>
                  <td class="mono">{s.slug}</td>
                  <td class="mono">{s.base_url || <span class="muted">—</span>}</td>
                  <td><span class={`badge ${s.active ? 'badge--on' : 'badge--off'}`}>{s.active ? 'active' : 'inactive'}</span></td>
                  <td class="table__actions row-actions">
                    <Button onClick={() => setEditing({ ...s })}>Edit</Button>
                    <Button variant="danger" onClick={() => void remove(s)}>Delete</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <EmptyState title="No services yet"><p>Add one below to start granting keys access to it.</p></EmptyState>;
      }}</Loading>
      <form class="card form-card" onSubmit={add.onSubmit}>
        <h2 class="card__title">Add a service</h2>
        {add.error && <Notice tone="error">{add.error}</Notice>}
        <div class="form-grid">
          <Field label="Slug" value={form.slug} onValue={(v) => setForm({ ...form, slug: v })} required
                 hint="The name the service checks grants by, e.g. marketrest. Can't be changed later." />
          <Field label="Name" value={form.display_name} onValue={(v) => setForm({ ...form, display_name: v })} required />
        </div>
        <Field label="Address" type="url" value={form.base_url} onValue={(v) => setForm({ ...form, base_url: v })} placeholder="https://market.example.com"
               hint="Optional — needed to test it from here." />
        <Field label="Description" value={form.description} onValue={(v) => setForm({ ...form, description: v })} />
        <div class="actions"><Button type="submit" variant="primary" busy={add.busy}>Add service</Button></div>
      </form>
    </>
  );
}
