/**
 * Output settings for a key: which coordinate systems, fields, angles and bodies the
 * API returns. Only the differences from the server's defaults are saved.
 */

import { useEffect, useState } from 'preact/hooks';
import { api, type OutputSettings } from '../api';
import { Link } from '../router';
import { Button, Checkbox, Loading, Notice, PageHeader, Select, describe, useLoad } from '../ui';

type Config = Record<string, unknown>;

const HOUSE_SYSTEMS: Array<[string, string]> = [
  ['', 'None (no house cusps)'], ['placidus', 'Placidus'], ['koch', 'Koch'], ['whole_sign', 'Whole Sign'],
  ['equal', 'Equal'], ['regiomontanus', 'Regiomontanus'], ['campanus', 'Campanus'], ['porphyrius', 'Porphyrius'],
  ['vehlow_equal', 'Vehlow Equal'], ['meridian', 'Meridian'], ['azimuthal', 'Azimuthal'], ['topocentric', 'Topocentric'],
  ['alcabitus', 'Alcabitus'], ['morinus', 'Morinus'], ['krusinski', 'Krusinski'], ['gauquelin', 'Gauquelin'],
];

const LABELS: Record<string, string> = {
  geocentric: 'Geocentric positions', heliocentric: 'Heliocentric positions', right_ascension: 'Right ascension',
  declination: 'Declination', longitude_speed: 'Longitude speed', latitude_speed: 'Latitude speed',
  declination_speed: 'Declination speed', retrograde: 'Retrograde flag',
  asc: 'Ascendant', mc: 'Midheaven (MC)', vertex: 'Vertex', east_point: 'East Point', armc: 'ARMC (sidereal time)',
  mean_node: 'Mean Node', true_node: 'True Node', south_node: 'South Node', part_of_fortune: 'Part of Fortune',
  mean_lilith: 'Mean Lilith', true_lilith: 'True Lilith', asteroids: 'Asteroids (all)',
  api_usage: 'API usage counts', from_cache: '"From cache" flag',
};

const SECTIONS: Array<[key: string | null, title: string, hint: string]> = [
  [null, 'Coordinates and fields', 'Returned for every body.'],
  ['angles', 'Angles', 'Geocentric only.'],
  ['bodies', 'Bodies', 'Turning Asteroids off hides Ceres, Pallas, Juno, Vesta and Chiron.'],
  ['meta', 'Response details', ''],
];

const label = (key: string) => LABELS[key] ?? key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, ' ');
const isObject = (v: unknown): v is Config => typeof v === 'object' && v !== null && !Array.isArray(v);

/** The parts of `value` that differ from `defaults` (one level of nesting). */
function differences(value: Config, defaults: Config): Config {
  const out: Config = {};
  for (const [key, v] of Object.entries(value)) {
    const d = defaults[key];
    if (isObject(v) && isObject(d)) {
      const inner = differences(v, d);
      if (Object.keys(inner).length) out[key] = inner;
    } else if (v !== d && !(v === '' && d === null)) {
      out[key] = v === '' ? null : v;
    }
  }
  return out;
}

export function Output({ keyId }: { keyId?: number }) {
  const path = keyId ? `/admin/keys/${keyId}/output` : '/me/output';
  const [loaded, reload] = useLoad(() => api<OutputSettings>(path), [path]);
  const [draft, setDraft] = useState<Config | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  useEffect(() => {
    if (loaded.state === 'ready') setDraft(structuredClone(loaded.data.effective));
  }, [loaded]);

  const save = async (config: Config | null, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await api(path, 'POST', { output_config: config });
      setMessage({ tone: 'success', text: done });
      reload();
    } catch (e) {
      setMessage({ tone: 'error', text: describe(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Output settings"
        intro={loaded.state === 'ready' ? `What the API returns for ${loaded.data.identifier}. A request can still ask for something different with its own "output" object.` : undefined}
        actions={keyId ? <Link href={`/keys/${keyId}`} class="btn">Back to key</Link> : undefined}
      />
      {message && <Notice tone={message.tone}>{message.text}</Notice>}
      <Loading result={loaded}>{() => {
        if (loaded.state !== 'ready' || !draft) return null;
        const defaults = loaded.data.defaults;
        const changes = differences(draft, defaults);
        const set = (section: string | null, key: string, value: unknown) => {
          const next = structuredClone(draft);
          if (section) (next[section] as Config)[key] = value;
          else next[key] = value;
          setDraft(next);
        };
        return (
          <>
            {SECTIONS.map(([section, title, hint]) => {
              const values = section ? (draft[section] as Config) : draft;
              const base = section ? (defaults[section] as Config) : defaults;
              const keys = Object.keys(values).filter((k) => typeof values[k] === 'boolean');
              return (
                <div class="card" key={title}>
                  <h2 class="card__title">{title}</h2>
                  {hint && <p class="muted small">{hint}</p>}
                  {section === null && (
                    <Select label="House system when a request doesn't name one" value={String(draft.default_house_system ?? '')}
                            onValue={(v) => set(null, 'default_house_system', v || null)} options={HOUSE_SYSTEMS} />
                  )}
                  <div class="option-grid">
                    {keys.map((key) => (
                      <Checkbox key={key} checked={Boolean(values[key])} onChecked={(v) => set(section, key, v)}
                                label={<>{label(key)}{values[key] !== base[key] && <span class="badge badge--custom">changed</span>}</>} />
                    ))}
                  </div>
                </div>
              );
            })}
            <div class="actions">
              <Button variant="primary" busy={busy} onClick={() => void save(changes, 'Output settings saved.')}>Save</Button>
              <Button onClick={() => setDraft(structuredClone(loaded.data.effective))}>Undo changes</Button>
              <Button busy={busy} onClick={() => void save(null, 'Back to the server defaults.')}>Use the defaults</Button>
            </div>
            <p class="muted small">{Object.keys(changes).length ? 'Differences from the defaults are marked "changed".' : 'All settings match the server defaults.'}</p>
          </>
        );
      }}</Loading>
    </>
  );
}
