/** The server's settings, read-only: they're set in the .env file on the server. */

import type { ComponentChildren } from 'preact';
import { api, type ServerSettings } from '../api';
import { Loading, PageHeader, useLoad } from '../ui';

const onOff = (v: boolean) => <span class={`badge ${v ? 'badge--on' : 'badge--off'}`}>{v ? 'on' : 'off'}</span>;

export function Settings() {
  const [loaded] = useLoad(() => api<ServerSettings & { email_configured: boolean; federation_config_dir: string }>('/admin/settings'), []);

  return (
    <>
      <PageHeader title="Settings" intro="Set in the server's .env file (in the standard install, /opt/ephemeral/.env) — change it there, then run `ephemeral restart`." />
      <Loading result={loaded}>{() => {
        if (loaded.state !== 'ready') return null;
        const s = loaded.data;
        const rows: Array<[string, ComponentChildren, string]> = [
          ['Address', s.public_url || <span class="muted">not set — email links fall back to the old settings</span>, 'PUBLIC_URL'],
          ['Name', s.site_name, 'SITE_NAME'],
          ['Version', s.version, ''],
          ['Database', <span class="mono">{s.database}</span>, 'DB_TYPE, DATABASE_PATH, MYSQL_*'],
          ['Place lookups', s.geocoding === 'google' ? 'Google Maps, with the cities list for suggestions' : 'Offline, from the cities list', 'USE_GOOGLE'],
          ['Cities imported', s.cities_imported === null ? '—' : s.cities_imported.toLocaleString(), '`ephemeral import-cities`'],
          ['Ephemeris files', s.ephemeris_files.length ? <span class="mono">{s.ephemeris_files.join(', ')}</span> : <span class="muted">none found</span>, '`ephemeral fetch-ephemeris`'],
          ['Email', onOff(s.email_configured), 'the Email page'],
          ['Anyone can register', onOff(s.allow_registration), 'ALLOW_REGISTRATION'],
          ['Admins can make admins', onOff(s.allow_admin_promotion), 'ALLOW_ADMIN_PROMOTION'],
          ['Rate limiting', onOff(s.rate_limiting), 'RATE_LIMIT_ENABLED'],
          ['Signed out after idle', `${Math.round(s.session_timeout / 60)} minutes`, 'SESSION_TIMEOUT (seconds)'],
          ['Longest session', `${s.session_max_hours} hours`, 'SESSION_MAX_HOURS'],
          ['Remembered devices', `${s.trusted_device_days} days`, 'TRUSTED_DEVICE_DAYS'],
          ['Other sites that may call the API', <span class="mono">{s.cors_origins.join(', ')}</span>, 'CORS_ORIGINS'],
          ['Account store', onOff(s.store_enabled), 'STORE_ENABLED'],
          ['Federation test files', <span class="mono">{s.federation_config_dir}</span>, 'FEDERATION_CONFIG_DIR'],
        ];
        return (
          <div class="card">
            <table class="table table--plain">
              <tbody>
                {rows.map(([label, value, where]) => (
                  <tr key={label}>
                    <th scope="row">{label}</th>
                    <td>{value}</td>
                    <td class="muted small">{where && (where.startsWith('`') || where.startsWith('the ') ? where.replace(/`/g, '') : <code>{where}</code>)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }}</Loading>
    </>
  );
}
