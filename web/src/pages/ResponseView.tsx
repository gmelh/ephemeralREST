/** A response from the API tester or the federation test: status, time and body. */

export interface ShownResponse {
  status: number;
  ms: number;
  text: string;
  url?: string;
  error?: string | null;
}

export function pretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

export function ResponseView({ response }: { response: ShownResponse | null }) {
  if (!response) return <p class="muted">Send a request to see the response here.</p>;
  const tone = response.status >= 200 && response.status < 300 ? 'stat--good' : response.status >= 400 || !response.status ? 'stat--bad' : 'stat--warn';
  return (
    <>
      <div class="stats">
        <div class={`stat ${tone}`}><strong>{response.status || '—'}</strong><span>Status</span></div>
        <div class="stat"><strong>{response.ms.toLocaleString()} ms</strong><span>Time</span></div>
      </div>
      {response.url && <p class="muted small mono">{response.url}</p>}
      {response.error && <p class="notice notice--error">{response.error}</p>}
      <pre class="pre">{pretty(response.text)}</pre>
    </>
  );
}
