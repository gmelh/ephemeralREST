/** Every API key (one per account), with its status and rate limits. */

import { useState } from 'preact/hooks';
import { api, type KeyRecord } from '../api';
import { Link } from '../router';
import { usePref, isBoolean } from '../prefs';
import { Checkbox, EmptyState, Loading, PageHeader, count, useLoad } from '../ui';

export function Keys() {
  const [showDisabled, setShowDisabled] = usePref('keys_show_disabled', false, isBoolean);
  const [search, setSearch] = useState('');
  const [keys] = useLoad(() => api<{ keys: KeyRecord[] }>(`/admin/keys${showDisabled ? '?inactive=1' : ''}`), [showDisabled]);

  const limit = (n: number | null) => (n === null ? <span class="muted">default</span> : n.toLocaleString());

  return (
    <>
      <PageHeader title="Keys" intro="Each account has one API key. Open one to change its limits, output, access or status." />
      <div class="toolbar">
        <input class="search" type="search" placeholder="Search by name or email" value={search}
               onInput={(e) => setSearch(e.currentTarget.value)} aria-label="Search keys" />
        <Checkbox label="Show disabled and unverified" checked={showDisabled} onChecked={setShowDisabled} />
      </div>
      <Loading result={keys}>{() => {
        if (keys.state !== 'ready') return null;
        const term = search.trim().toLowerCase();
        const shown = keys.data.keys.filter((k) => !term || k.identifier.toLowerCase().includes(term) || k.name.toLowerCase().includes(term));
        if (!shown.length) return <EmptyState title={term ? 'No keys match' : 'No keys yet'}><p>People get a key when they register.</p></EmptyState>;
        return (
          <>
            <div class="table-scroll">
              <table class="table">
                <thead>
                  <tr><th>Email</th><th>Name</th><th>Prefix</th><th>Status</th><th>Per minute</th><th>Per hour</th><th>Per day</th></tr>
                </thead>
                <tbody>
                  {shown.map((k) => (
                    <tr key={k.id}>
                      <td>
                        <Link href={`/keys/${k.id}`} class="row-link">{k.identifier}</Link>
                        {Boolean(k.admin) && <span class="badge badge--admin" style="margin-left:8px">admin</span>}
                      </td>
                      <td>{k.name}</td>
                      <td class="mono">{k.key_prefix}</td>
                      <td><span class={`badge ${k.active ? 'badge--on' : 'badge--off'}`}>{k.active ? 'active' : 'disabled'}</span></td>
                      <td>{limit(k.rate_per_minute)}</td>
                      <td>{limit(k.rate_per_hour)}</td>
                      <td>{limit(k.rate_per_day)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p class="muted small">{count(shown.length, 'key')}</p>
          </>
        );
      }}</Loading>
    </>
  );
}
