/**
 * Try the API from the browser: pick an endpoint (or type any path), fill in its
 * parameters and body, and send it — as yourself, or with an API key you paste in.
 */

import { useMemo, useState } from 'preact/hooks';
import { rawRequest } from '../api';
import endpoints from '../endpoints.json';
import { Button, Field, Notice, PageHeader, describe } from '../ui';
import { ResponseView, type ShownResponse } from './ResponseView';

interface Param { key: string; label?: string; placeholder?: string; required?: boolean }
interface Endpoint {
  group: string; method: string; path: string; label: string; desc: string;
  body: unknown; path_params: Param[]; params: Param[];
}

const CATALOG = endpoints as Endpoint[];
const GROUPS = [...new Set(CATALOG.map((e) => e.group))];

export function ApiTester() {
  const [group, setGroup] = useState(GROUPS[0]!);
  const [chosen, setChosen] = useState<Endpoint | null>(null);
  const [method, setMethod] = useState('GET');
  const [path, setPath] = useState('/health');
  const [values, setValues] = useState<Record<string, string>>({});
  const [body, setBody] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<ShownResponse | null>(null);

  const choose = (e: Endpoint) => {
    setChosen(e);
    setMethod(e.method);
    setPath(e.path);
    setValues({});
    setBody(e.body ? JSON.stringify(e.body, null, 2) : '');
    setResponse(null);
    setError(null);
  };

  /** The path with {placeholders} and query parameters filled in. */
  const resolved = useMemo(() => {
    let p = path;
    for (const param of chosen?.path_params ?? []) {
      p = p.replace(`{${param.key}}`, encodeURIComponent(values[param.key] ?? '') || `{${param.key}}`);
    }
    const query = (chosen?.params ?? []).filter((q) => values[q.key]).map((q) => `${q.key}=${encodeURIComponent(values[q.key]!)}`);
    return query.length ? `${p}${p.includes('?') ? '&' : '?'}${query.join('&')}` : p;
  }, [path, values, chosen]);

  const send = async (event: Event) => {
    event.preventDefault();
    setError(null);
    if (body.trim()) {
      try {
        JSON.parse(body);
      } catch {
        setError("The body isn't valid JSON.");
        return;
      }
    }
    if (/\{\w+\}/.test(resolved)) {
      setError('Fill in the parameters in the path first.');
      return;
    }
    setBusy(true);
    try {
      const r = await rawRequest(method, resolved, method === 'GET' ? '' : body, apiKey.trim());
      setResponse({ status: r.status, ms: r.ms, text: r.text, url: `/api/v1${resolved}` });
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="API tester" intro="Requests go to /api/v1 as you (your session), or with an API key if you paste one in." />
      <div class="tester">
        <div>
          <div class="card">
            <div class="tabs">
              {GROUPS.map((g) => (
                <button type="button" key={g} class={g === group ? 'is-active' : ''} onClick={() => setGroup(g)}>{g}</button>
              ))}
            </div>
            <div class="examples">
              {CATALOG.filter((e) => e.group === group).map((e) => (
                <button type="button" key={e.label} class={`btn${chosen === e ? ' btn--primary' : ''}`} onClick={() => choose(e)} title={e.desc}>
                  {e.label}
                </button>
              ))}
            </div>
            {chosen && <p class="muted small">{chosen.desc}</p>}
          </div>
          <form class="card form-card" onSubmit={send}>
            {error && <Notice tone="error">{error}</Notice>}
            <div class="tester__line">
              <select aria-label="Method" value={method} onChange={(e) => setMethod(e.currentTarget.value)}>
                {['GET', 'POST', 'PUT', 'DELETE'].map((m) => <option key={m}>{m}</option>)}
              </select>
              <input aria-label="Path" class="mono" value={path} onInput={(e) => { setPath(e.currentTarget.value); setChosen(null); }} />
            </div>
            {[...(chosen?.path_params ?? []), ...(chosen?.params ?? [])].map((p) => (
              <Field key={p.key} label={p.label || p.key} value={values[p.key] ?? ''} placeholder={p.placeholder}
                     onValue={(v) => setValues({ ...values, [p.key]: v })} required={p.required} />
            ))}
            {method !== 'GET' && (
              <label class="field" for="tester-body">
                <span>Body (JSON)</span>
                <textarea id="tester-body" class="code mono" rows={10} value={body} onInput={(e) => setBody(e.currentTarget.value)} />
              </label>
            )}
            <Field label="API key" type="password" value={apiKey} onValue={setApiKey} autoComplete="off"
                   hint="Optional. Leave empty to send the request as yourself." />
            <p class="muted small mono">{method} /api/v1{resolved}</p>
            <div class="actions"><Button type="submit" variant="primary" busy={busy}>Send</Button></div>
          </form>
        </div>
        <div class="card">
          <h2 class="card__title">Response</h2>
          <ResponseView response={response} />
        </div>
      </div>
    </>
  );
}
