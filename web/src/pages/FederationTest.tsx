/**
 * Send a request to a federated service with your own key — it fails exactly as a
 * real caller's would if the key hasn't been granted access.
 */

import { useState } from 'preact/hooks';
import { api } from '../api';
import { Link } from '../router';
import { Button, EmptyState, Field, Loading, Notice, PageHeader, Select, describe, useLoad } from '../ui';
import { ResponseView, type ShownResponse } from './ResponseView';

interface Param { name: string; in?: 'path' | 'query'; required?: boolean; placeholder?: string }
interface Endpoint { method: string; path: string; group?: string; description?: string; body?: unknown; params?: Param[] }
interface Service { slug: string; display_name: string; description: string; base_url: string; endpoints: Endpoint[]; source: string }

export function FederationTest() {
  const [loaded] = useLoad(() => api<{ services: Service[]; config_dir: string }>('/admin/federation-test/services'), []);
  const [slug, setSlug] = useState('');
  const [endpoint, setEndpoint] = useState<Endpoint | null>(null);
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('/');
  const [values, setValues] = useState<Record<string, string>>({});
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<ShownResponse | null>(null);

  const choose = (e: Endpoint) => {
    setEndpoint(e);
    setMethod(e.method.toUpperCase());
    setPath(e.path);
    setValues({});
    setBody(e.body ? JSON.stringify(e.body, null, 2) : '');
    setResponse(null);
  };

  const send = async (service: Service) => {
    setError(null);
    let resolved = path;
    const query: Record<string, string> = {};
    for (const p of endpoint?.params ?? []) {
      const v = values[p.name] ?? '';
      if (p.required && !v.trim()) {
        setError(`Fill in ${p.name}.`);
        return;
      }
      if ((p.in ?? 'path') === 'path') resolved = resolved.replace(`{${p.name}}`, encodeURIComponent(v));
      else if (v) query[p.name] = v;
    }
    let parsed: unknown = null;
    if (body.trim() && method !== 'GET') {
      try {
        parsed = JSON.parse(body);
      } catch {
        setError("The body isn't valid JSON.");
        return;
      }
    }
    setBusy(true);
    try {
      const r = await api<{ status: number; time_ms: number; body: string; url: string; error: string | null }>(
        '/admin/federation-test/send', 'POST', { service: service.slug, method, path: resolved, query, body: parsed });
      setResponse({ status: r.status, ms: r.time_ms, text: r.body, url: r.url, error: r.error });
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p class="crumbs"><Link href="/services">Federated services</Link></p>
      <PageHeader title="Test a federated service" intro="Requests are sent from the server with your own API key." />
      <Loading result={loaded}>{() => {
        if (loaded.state !== 'ready') return null;
        const services = loaded.data.services;
        if (!services.length) {
          return (
            <EmptyState title="Nothing to test yet">
              <p>Give a service an address on the <Link href="/services">Services</Link> page, or add a test file for it
                to <code>{loaded.data.config_dir}</code> (one JSON file per service, listing its endpoints).</p>
            </EmptyState>
          );
        }
        const service = services.find((s) => s.slug === slug) ?? services[0]!;
        return (
          <div class="tester">
            <div>
              <div class="card form-card">
                <Select label="Service" value={service.slug} onValue={(v) => { setSlug(v); setEndpoint(null); setPath('/'); setResponse(null); }}
                        options={services.map((s) => [s.slug, s.display_name])} />
                <p class="muted small mono">{service.base_url}</p>
                {service.description && <p class="muted small">{service.description}</p>}
                {service.endpoints.length > 0 && (
                  <div class="examples">
                    {service.endpoints.map((e, i) => (
                      <button type="button" key={i} class={`btn${endpoint === e ? ' btn--primary' : ''}`} onClick={() => choose(e)} title={e.description}>
                        {e.method.toUpperCase()} {e.path}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <form class="card form-card" onSubmit={(ev) => { ev.preventDefault(); void send(service); }}>
                {error && <Notice tone="error">{error}</Notice>}
                <div class="tester__line">
                  <select aria-label="Method" value={method} onChange={(e) => setMethod(e.currentTarget.value)}>
                    {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => <option key={m}>{m}</option>)}
                  </select>
                  <input aria-label="Path" class="mono" value={path} onInput={(e) => { setPath(e.currentTarget.value); setEndpoint(null); }} />
                </div>
                {endpoint?.description && <p class="muted small">{endpoint.description}</p>}
                {(endpoint?.params ?? []).map((p) => (
                  <Field key={p.name} label={`${p.name}${(p.in ?? 'path') === 'query' ? ' (query)' : ''}`} value={values[p.name] ?? ''}
                         placeholder={p.placeholder} required={p.required} onValue={(v) => setValues({ ...values, [p.name]: v })} />
                ))}
                {method !== 'GET' && (
                  <label class="field" for="fed-body">
                    <span>Body (JSON)</span>
                    <textarea id="fed-body" class="code mono" rows={8} value={body} onInput={(e) => setBody(e.currentTarget.value)} />
                  </label>
                )}
                <div class="actions"><Button type="submit" variant="primary" busy={busy}>Send</Button></div>
              </form>
            </div>
            <div class="card">
              <h2 class="card__title">Response</h2>
              <ResponseView response={response} />
            </div>
          </div>
        );
      }}</Loading>
    </>
  );
}
