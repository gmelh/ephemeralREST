/** The administrator's home: is the server healthy, and what's in it. */

import { api, type Health, type KeyRecord } from '../api';
import { config } from '../config';
import { Link } from '../router';
import { Loading, PageHeader, useLoad } from '../ui';

interface Stats {
  cache_statistics: Record<string, number | null | Record<string, unknown>>;
  api_usage: Record<string, unknown>;
}

const STAT_LABELS: Array<[string, string]> = [
  ['charts_cached', 'Charts'],
  ['derived_charts', 'Derived charts'],
  ['canonical_places', 'Places'],
  ['place_cache_active', 'Cached lookups'],
  ['cities_loaded', 'Cities'],
];

export function Dashboard() {
  const [health] = useLoad(() => api<Health>('/health'), []);
  const [stats] = useLoad(() => api<Stats>('/cache/stats'), []);
  const [keys] = useLoad(() => api<{ keys: KeyRecord[] }>('/admin/keys?inactive=1'), []);

  return (
    <>
      <PageHeader title="Dashboard" intro="Server status and what's stored." actions={<Link href="/keys" class="btn btn--primary">Keys</Link>} />
      <div class="stats">
        <div class={`stat ${health.state === 'ready' && health.data.status === 'healthy' ? 'stat--good' : health.state === 'error' ? 'stat--bad' : ''}`}>
          <strong>{health.state === 'ready' ? (health.data.status === 'healthy' ? 'Healthy' : health.data.status) : health.state === 'error' ? 'Unwell' : '…'}</strong>
          <span>Server</span>
        </div>
        {keys.state === 'ready' && (
          <>
            <div class="stat"><strong>{keys.data.keys.filter((k) => k.active).length.toLocaleString()}</strong><span>Active keys</span></div>
            <div class="stat"><strong>{keys.data.keys.filter((k) => !k.active).length.toLocaleString()}</strong><span>Disabled or unverified</span></div>
          </>
        )}
        {stats.state === 'ready' && STAT_LABELS.map(([key, label]) => {
          const value = stats.data.cache_statistics[key];
          return typeof value === 'number'
            ? <div class="stat" key={key}><strong>{value.toLocaleString()}</strong><span>{label}</span></div>
            : null;
        })}
      </div>
      <div class="columns">
        <div class="card">
          <h2 class="card__title">Server</h2>
          <Loading result={health}>{() => health.state === 'ready' && (
            <dl class="facts">
              <div><dt>Address</dt><dd class="mono">{config.public_url || location.origin}</dd></div>
              <div><dt>API</dt><dd class="mono">{(config.public_url || location.origin) + '/api/v1'}</dd></div>
              <div><dt>Version</dt><dd>{health.data.version}</dd></div>
              <div><dt>Server time</dt><dd class="mono">{health.data.timestamp.replace('T', ' ').slice(0, 19)}</dd></div>
              <div><dt>Bodies</dt><dd>{health.data.celestial_bodies?.length ?? '—'}</dd></div>
              <div><dt>House systems</dt><dd>{health.data.supported_house_systems?.length ?? '—'}</dd></div>
            </dl>
          )}</Loading>
        </div>
        <div class="card">
          <h2 class="card__title">Lookups this month</h2>
          <Loading result={stats}>{() => stats.state === 'ready' && (
            <dl class="facts">
              {Object.entries(stats.data.api_usage ?? {}).map(([key, value]) => (
                <div key={key}><dt>{key.replace(/_/g, ' ')}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>
              ))}
            </dl>
          )}</Loading>
        </div>
      </div>
    </>
  );
}
