/** The default rate limits, for every key without its own. */

import { useEffect, useState } from 'preact/hooks';
import { api, type ClassLimits } from '../api';
import { Button, Field, Loading, Notice, PageHeader, useLoad, useSubmit } from '../ui';

export function RateLimits() {
  const [loaded, reload] = useLoad(() => api<ClassLimits>('/admin/class-limits'), []);
  const [values, setValues] = useState({ rpm: '', rph: '', rpd: '' });
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (loaded.state === 'ready') {
      setValues({ rpm: String(loaded.data.rate_per_minute), rph: String(loaded.data.rate_per_hour), rpd: String(loaded.data.rate_per_day) });
    }
  }, [loaded]);

  const { busy, error, onSubmit } = useSubmit(async () => {
    setSaved(false);
    await api('/admin/class-limits', 'POST', {
      rate_per_minute: Number(values.rpm), rate_per_hour: Number(values.rph), rate_per_day: Number(values.rpd),
    });
    setSaved(true);
    reload();
  });

  return (
    <>
      <PageHeader title="Rate limits" intro="How many requests a key can make, unless it has limits of its own. Administrators aren't limited." />
      <Loading result={loaded}>{() => (
        <form class="card form-card" onSubmit={onSubmit}>
          {error && <Notice tone="error">{error}</Notice>}
          {saved && <Notice tone="success">Default limits saved.</Notice>}
          <div class="form-grid">
            <Field label="Per minute" type="number" value={values.rpm} onValue={(v) => setValues({ ...values, rpm: v })} required />
            <Field label="Per hour" type="number" value={values.rph} onValue={(v) => setValues({ ...values, rph: v })} required />
            <Field label="Per day" type="number" value={values.rpd} onValue={(v) => setValues({ ...values, rpd: v })} required />
          </div>
          <div class="actions"><Button type="submit" variant="primary" busy={busy}>Save</Button></div>
          <p class="muted small">The per-minute limit applies to chart calculations; the hourly and daily limits apply to every request.</p>
        </form>
      )}</Loading>
    </>
  );
}
