/** What administrators changed (everything under /admin), newest first. */

import { useEffect, useState } from 'preact/hooks';
import { api, type AdminAction } from '../api';
import { Button, Notice, PageHeader, describe } from '../ui';
import { ActionList } from './Sites';

export function Activity() {
  const [rows, setRows] = useState<AdminAction[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = (before: number | null) => {
    api<{ data: AdminAction[]; next: number | null }>(`/admin/audit${before ? `?before=${before}` : ''}`)
      .then((page) => { setRows((shown) => [...(before && shown ? shown : []), ...page.data]); setNext(page.next); })
      .catch((e: unknown) => setError(describe(e)));
  };
  useEffect(() => load(null), []);
  return (
    <>
      <PageHeader title="Activity" intro="What administrators changed: keys, sites, limits, email and settings. Passwords, keys and tokens are never recorded." />
      {error && <Notice tone="error">{error}</Notice>}
      {rows && <div class="card"><ActionList rows={rows} showTarget /></div>}
      {next && <div class="actions"><Button onClick={() => load(next)}>Load more</Button></div>}
    </>
  );
}
