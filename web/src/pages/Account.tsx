/** Your own account: details, rate limits, a new API key, password and devices. */

import { useState } from 'preact/hooks';
import { api, type User } from '../api';
import { config } from '../config';
import { Link } from '../router';
import { useSession } from '../session';
import { Button, CopyText, Loading, Notice, PageHeader, describe, useLoad } from '../ui';

export function Account() {
  const { session, signOut } = useSession();
  const [me] = useLoad(() => api<User>('/me'), []);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [rotating, setRotating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rotate = async () => {
    if (!confirm('Make a new API key? Your current key stops working straight away, in every app that uses it.')) return;
    setRotating(true);
    setError(null);
    try {
      const result = await api<{ api_key: string }>('/me/rotate', 'POST');
      setNewKey(result.api_key);
    } catch (e) {
      setError(describe(e));
    } finally {
      setRotating(false);
    }
  };

  const limit = (n: number | null) => (n === null ? 'Unlimited' : n.toLocaleString());

  return (
    <>
      <PageHeader title={session.user.name || 'My account'} intro={session.user.identifier} />
      {error && <Notice tone="error">{error}</Notice>}
      {session.user.admin && config.admin_here === false && config.admin_url && (
        <Notice tone="info">Administration is at <a href={config.admin_url}>{config.admin_url.replace(/^https?:\/\//, '')}</a>.</Notice>
      )}
      {newKey && (
        <div class="card">
          <h2 class="card__title">Your new API key</h2>
          <Notice tone="success">Copy it now — it's shown only once. It has also been emailed to you.</Notice>
          <CopyText text={newKey} />
        </div>
      )}
      <div class="columns">
        <div class="card">
          <h2 class="card__title">Account</h2>
          <Loading result={me}>{() => me.state === 'ready' && (
            <dl class="facts">
              <div><dt>Name</dt><dd>{me.data.name}</dd></div>
              <div><dt>Email</dt><dd>{me.data.identifier}</dd></div>
              <div><dt>Role</dt><dd><span class="badge">{me.data.admin ? 'administrator' : 'user'}</span></dd></div>
              <div><dt>Requests per minute</dt><dd>{limit(me.data.rate_limits.per_minute)}</dd></div>
              <div><dt>Requests per hour</dt><dd>{limit(me.data.rate_limits.per_hour)}</dd></div>
              <div><dt>Requests per day</dt><dd>{limit(me.data.rate_limits.per_day)}</dd></div>
            </dl>
          )}</Loading>
        </div>
        <div>
          <div class="card">
            <h2 class="card__title">API key</h2>
            <p class="muted">Apps call the API with your key in an <code>X-API-Key</code> header. If it's been shared or
              lost, make a new one: the old key stops working straight away.</p>
            <div class="actions"><Button onClick={rotate} busy={rotating}>Make a new key</Button></div>
          </div>
          <div class="card">
            <h2 class="card__title">Output</h2>
            <p class="muted">Choose which bodies and fields the API returns for your key.</p>
            <div class="actions"><Link href="/output" class="btn">Output settings</Link></div>
          </div>
          <div class="card">
            <h2 class="card__title">Password and devices</h2>
            <div class="actions">
              <Link href={`/set-password?email=${encodeURIComponent(session.user.identifier)}`} class="btn">Change password</Link>
              <Button onClick={() => void signOut(true)}>Log out and forget this device</Button>
            </div>
            <p class="muted small">Forgetting this device means it asks for an emailed code at the next log in.</p>
          </div>
        </div>
      </div>
    </>
  );
}
